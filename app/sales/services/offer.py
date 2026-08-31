"""SalesOffer service layer (WP-8 PR-1). Creation and the container-based
autosave/completeness mechanics are split deliberately: PR-1 ships the bare
entity + grid; PR-2 adds `update_offer`'s container-completeness contract.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import CustomerLifecycleStatus, get_customer_or_404
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.offer import OfferContainerState, OfferUpdate
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_offer_number
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot

_EVENT_PRODUCER = "sales"


def _resolve_customer_label(customer) -> str:
    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def vehicle_condition(offer: SalesOffer) -> str | None:
    """A manual configuration's condition lives on its own column; a
    stock vehicle's lives only inside the frozen vehicle_snapshot JSON
    (ADR-041) — one place to read either, shared by the API's own
    OfferRead.vehicle_condition and services/line_items.py's used-vehicle
    discount-suppression rule.
    """

    if offer.vehicle_source == "manual":
        return offer.manual_vehicle_condition
    if offer.vehicle_snapshot:
        return offer.vehicle_snapshot.get("condition")
    return None


def compute_offer_containers(offer: SalesOffer) -> list[OfferContainerState]:
    """Server-computed, never client-derived (FR-S-05) — the sticky
    footer's missing-requirements list and each container's own status
    badge both read from this one function, so they can never disagree.

    Requirement levels and the "placeholder" status for Leasing are fixed
    properties of the container itself (confirmed live against the
    reference prototype); everything else is derived from what has
    actually been filled in so far.
    """

    has_vehicle = offer.vehicle_source is not None and (
        offer.stock_item_id is not None or offer.vehicle_label is not None
    )
    # WP-8 PR-3 — "complete" means a REAL price exists to build from, not
    # merely "gross_price is not None" (build_up() always materializes a
    # value, including a legitimate Decimal(0) for an unpriced manual
    # config — that is not the same thing as "priced").
    has_base_price = (offer.vehicle_source == "manual" and offer.manual_base_price is not None) or (
        offer.vehicle_source == "stock"
        and offer.vehicle_snapshot is not None
        and offer.vehicle_snapshot.get("basePrice") is not None
    )
    return [
        OfferContainerState(
            id="customer",
            requirement="required",
            status="complete" if offer.customer_id is not None else "not_started",
        ),
        OfferContainerState(
            id="vehicle", requirement="required", status="complete" if has_vehicle else "not_started"
        ),
        OfferContainerState(
            id="pricing",
            requirement="required",
            status="complete" if has_base_price else ("in_progress" if has_vehicle else "not_started"),
        ),
        OfferContainerState(
            id="trade_in",
            requirement="optional",
            status="complete" if offer.trade_in_vehicle_id is not None else "not_started",
        ),
        OfferContainerState(
            id="leasing",
            requirement="optional",
            # S-D03 — never a real calculator in v1; "placeholder" is a
            # genuine third status, not "complete" dressed up.
            status="placeholder",
        ),
    ]


def get_offer_or_404(db: Session, tenant_id: uuid.UUID, offer_id: uuid.UUID) -> SalesOffer:
    offer = db.scalar(select(SalesOffer).where(SalesOffer.id == offer_id, SalesOffer.tenant_id == tenant_id))
    if offer is None:
        raise NotFoundError(f"Offer {offer_id} was not found.")
    return offer


def create_offer(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None) -> SalesOffer:
    offer = SalesOffer(
        tenant_id=tenant_id,
        offer_number=allocate_offer_number(db, tenant_id),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(offer)
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.offer.created",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_offer",
            aggregate_id=offer.id,
            payload={"offerNumber": offer.offer_number},
        ),
    )
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def update_offer(
    db: Session, *, offer: SalesOffer, group_id: uuid.UUID, data: OfferUpdate, actor_id: uuid.UUID | None
) -> SalesOffer:
    """The autosave PATCH (FR-S-05) — applied incrementally, no field
    required, any order. `customer_id` is resolved server-side into a
    denormalized label/locality on every change (never trusted from the
    client) — see the module's "split by staleness class" rule.
    """

    if offer.status != OfferStatus.DRAFT:
        raise ConflictError(f"Offer {offer.offer_number} can no longer be edited (status '{offer.status.value}').")

    changes = data.model_dump(exclude_unset=True)

    if "customer_id" in changes:
        customer_id = changes.pop("customer_id")
        if customer_id is None:
            offer.customer_id = None
            offer.customer_label = None
            offer.customer_language = None
            offer.customer_denorm_refreshed_at = None
        else:
            customer = get_customer_or_404(db, group_id, customer_id)
            # ADR-065/S-D19 — do-not-contact stops BOTH the offer and the
            # contract (it's about contact, not credit); a credit block is
            # the opposite case and deliberately does NOT stop here — it
            # only stops confirm_contract (S-D19: "quoting a blocked
            # customer is often how the block gets resolved").
            if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
                raise ConflictError(
                    f"Customer {customer.customer_number} is do-not-contact — cannot be attached to an offer."
                )
            offer.customer_id = customer.id
            offer.customer_label = _resolve_customer_label(customer)
            # CLAUDE.md's own rule: "the customer's correspondence language
            # is not the user's UI language" — denormalized here so
            # services/document.py never has to reach back into customer
            # at generation time.
            offer.customer_language = customer.language.value
            # customer_locality needs an address lookup app.customer.public
            # doesn't expose yet — left None here rather than guessed;
            # picked up once that surface exists (flagged, not silently
            # dropped).
            offer.customer_denorm_refreshed_at = utcnow()

    for field, value in changes.items():
        setattr(offer, field, value)

    offer.updated_by = actor_id
    offer.version += 1
    db.flush()

    # WP-8 PR-3 — re-derive pricing on every touch, not behind a separate
    # "build" button: FR-S-05's autosave is live, and freeze_vehicle_snapshot
    # is itself idempotent per vehicle identity, so this is cheap and safe
    # to call unconditionally once a vehicle exists.
    if freeze_vehicle_snapshot(db, offer=offer):
        db.flush()
    if offer.vehicle_snapshot is not None:
        apply_build_up(db, offer=offer)
        db.flush()

    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def finalize_offer(db: Session, *, offer: SalesOffer, actor_id: uuid.UUID | None) -> SalesOffer:
    """WP-8 PR-8 (ADR-063) — the second half of "build, then review": the
    seller has already generated at least one document
    (POST .../documents, PR-7) and reviewed it beside the margin panel;
    this is the explicit confirmation that ends the draft-editing phase.
    No new outbox event — sales.offer.created already fired at bare
    creation (ADR-005: events are facts, not commands, and nothing
    downstream needs to know an offer stopped being editable) — only
    `status` changes here, which the deal grid already reflects via
    upsert_deal_projection.
    """

    if offer.status != OfferStatus.DRAFT:
        raise ConflictError(f"Offer {offer.offer_number} is not a draft (status '{offer.status.value}').")

    missing = [c.id for c in compute_offer_containers(offer) if c.requirement == "required" and c.status != "complete"]
    if missing:
        raise ConflictError(
            f"Offer {offer.offer_number} is missing required containers: {', '.join(missing)}.",
            details={"missingContainers": missing},
        )

    offer.status = OfferStatus.OPEN
    offer.updated_by = actor_id
    offer.version += 1
    db.flush()

    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def cancel_offer(db: Session, *, offer: SalesOffer, reason: str, actor_id: uuid.UUID | None) -> SalesOffer:
    if offer.status == OfferStatus.CANCELLED:
        raise ConflictError(f"Offer {offer.offer_number} is already cancelled.")

    offer.status = OfferStatus.CANCELLED
    offer.cancelled_reason = reason
    offer.updated_by = actor_id
    offer.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.offer.cancelled",
            tenant_id=offer.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_offer",
            aggregate_id=offer.id,
            payload={"offerNumber": offer.offer_number, "reason": reason},
        ),
    )
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def list_offers(
    db: Session, *, tenant_id: uuid.UUID, params: SortPageParams
) -> tuple[list[SalesOffer], str | None, int, bool]:
    stmt = select(SalesOffer).where(SalesOffer.tenant_id == tenant_id)
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesOffer, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
