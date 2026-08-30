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
from app.customer.public import get_customer_or_404
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.offer import OfferContainerState, OfferUpdate
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_offer_number

_EVENT_PRODUCER = "sales"


def _resolve_customer_label(customer) -> str:
    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


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
            # Pricing itself (PR-3) has nothing to fill in yet — "in
            # progress" once a vehicle exists is the honest status until
            # then, never "complete" for a container with no real fields.
            status="in_progress" if has_vehicle else "not_started",
        ),
        OfferContainerState(
            id="trade_in",
            requirement="optional",
            # No trade-in fields exist on the offer yet (PR-5) — always
            # "not_started" until then.
            status="not_started",
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
            offer.customer_denorm_refreshed_at = None
        else:
            customer = get_customer_or_404(db, group_id, customer_id)
            offer.customer_id = customer.id
            offer.customer_label = _resolve_customer_label(customer)
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
