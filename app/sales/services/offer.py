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
from app.customer.public import Customer, CustomerLifecycleStatus, get_customer_or_404
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.deal_editing import apply_vehicle_and_pricing_changes
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_offer_number
from app.sales.services.priced_deal import PricedDeal, compute_containers, require_editable
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot

_EVENT_PRODUCER = "sales"


def resolve_customer_label(customer: Customer) -> str:
    """Shared with contract.py's own customer_id-sourced denormalization
    (KAN-58) — one label rule for both "attach a customer to an offer" and
    "create a contract directly for a customer"."""

    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def apply_customer_change(
    db: Session, *, deal: PricedDeal, group_id: uuid.UUID, customer_id: uuid.UUID | None, host: str
) -> None:
    """Sets or clears the buyer of an offer draft or a pending contract —
    one rule for both (the contract's since KAN-307, Anto 2026-10-12:
    "the same as offer generation"; ADR-083 amended). `customer_id` is
    resolved server-side into the denormalized label and correspondence
    language on every change (never trusted from the client). `host` ends
    the do-not-contact refusal ("an offer", "a contract"). The caller has
    checked the deal is editable and owns the transaction."""

    if customer_id is None:
        deal.customer_id = None
        deal.customer_label = None
        deal.customer_language = None
        deal.customer_denorm_refreshed_at = None
        return
    customer = get_customer_or_404(db, group_id, customer_id)
    # ADR-065/S-D19 — do-not-contact stops BOTH the offer and the
    # contract (it's about contact, not credit); a credit block is
    # the opposite case and deliberately does NOT stop here — it
    # only stops confirm_contract (S-D19: "quoting a blocked
    # customer is often how the block gets resolved").
    if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
        raise ConflictError(f"Customer {customer.customer_number} is do-not-contact — cannot be attached to {host}.")
    deal.customer_id = customer.id
    deal.customer_label = resolve_customer_label(customer)
    # CLAUDE.md's own rule: "the customer's correspondence language
    # is not the user's UI language" — denormalized here so
    # services/document.py never has to reach back into customer
    # at generation time.
    deal.customer_language = customer.language.value
    # customer_locality needs an address lookup app.customer.public
    # doesn't expose yet — left None here rather than guessed;
    # picked up once that surface exists (flagged, not silently
    # dropped).
    deal.customer_denorm_refreshed_at = utcnow()


def get_offer_or_404(
    db: Session, tenant_id: uuid.UUID, offer_id: uuid.UUID, *, for_update: bool = False
) -> SalesOffer:
    """`for_update` (KAN-311) locks the row until the caller commits, as
    `get_contract_or_404` does since KAN-288: every route that changes an
    offer loads it this way, so two saves of one offer run one after the
    other and the second is refused as stale. Without it both passed the
    If-Match check against their own read and the later one overwrote the
    first (a lost update), and an edit could land on an offer finalised or
    cancelled in between. The offer is the first row in the offer ->
    contract -> deal lock order (KAN-147)."""

    stmt = select(SalesOffer).where(SalesOffer.id == offer_id, SalesOffer.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    offer = db.scalar(stmt)
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

    require_editable(offer)

    changes = data.model_dump(exclude_unset=True)

    if "customer_id" in changes:
        apply_customer_change(db, deal=offer, group_id=group_id, customer_id=changes.pop("customer_id"), host="an offer")

    apply_vehicle_and_pricing_changes(db, deal=offer, changes=changes, actor_id=actor_id, host="offer workspace")

    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def copy_offer(db: Session, *, source: SalesOffer, actor_id: uuid.UUID | None) -> SalesOffer:
    """KAN-12 / PRD-Sales v2: "Copy Offer (new offerId, prefilled)" — the
    action available on a `pending`/`open` offer, so the seller can start
    a fresh negotiation from an already-configured one without retyping
    everything. Starts a genuinely new lineage: a fresh `id`, `version=1`
    (via the model default), a fresh `offer_number`, `status=DRAFT` —
    never the source offer's own status, since the whole point is a new
    draft to edit. `copied_from_offer_id` records where it came from.

    Copies the identifying/configuration fields only (customer, vehicle
    reference, discount, trade-in reference, leasing) — never the
    source's own frozen `vehicle_snapshot`/computed pricing verbatim.
    Those are re-derived immediately below via the exact same
    `freeze_vehicle_snapshot`/`apply_build_up` calls `update_offer` makes
    on every touch, so a copy reflects current stock/pricing data, not a
    stale echo of whatever the source happened to show when it was
    generated.
    """

    offer = SalesOffer(
        tenant_id=source.tenant_id,
        offer_number=allocate_offer_number(db, source.tenant_id),
        copied_from_offer_id=source.id,
        customer_id=source.customer_id,
        customer_label=source.customer_label,
        customer_locality=source.customer_locality,
        customer_language=source.customer_language,
        customer_denorm_refreshed_at=source.customer_denorm_refreshed_at,
        vehicle_source=source.vehicle_source,
        stock_item_id=source.stock_item_id,
        vehicle_label=source.vehicle_label,
        manual_vehicle_condition=source.manual_vehicle_condition,
        manual_base_price=source.manual_base_price,
        # C-F (KAN-10): a configured car stays configured on the copy — same
        # configuration reference, re-frozen below like everything else.
        configuration_id=source.configuration_id,
        configuration_label=source.configuration_label,
        configuration_label_refreshed_at=source.configuration_label_refreshed_at,
        discount_type=source.discount_type,
        discount_value=source.discount_value,
        trade_in_vehicle_id=source.trade_in_vehicle_id,
        trade_in_label=source.trade_in_label,
        trade_in_vin=source.trade_in_vin,
        trade_in_valuation_id=source.trade_in_valuation_id,
        trade_in_configuration_id=source.trade_in_configuration_id,
        trade_in_configuration_label_refreshed_at=source.trade_in_configuration_label_refreshed_at,
        trade_in_value=source.trade_in_value,
        trade_in_purchase_price=source.trade_in_purchase_price,
        leasing_down_payment=source.leasing_down_payment,
        leasing_term_months=source.leasing_term_months,
        leasing_km_per_year=source.leasing_km_per_year,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(offer)
    db.flush()

    if freeze_vehicle_snapshot(db, deal=offer):
        db.flush()
    if offer.vehicle_snapshot is not None:
        apply_build_up(db, deal=offer)
        db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.offer.created",
            tenant_id=source.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_offer",
            aggregate_id=offer.id,
            payload={"offerNumber": offer.offer_number, "copiedFromOfferId": str(source.id)},
        ),
    )
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

    missing = [c.id for c in compute_containers(offer) if c.requirement == "required" and c.status != "complete"]
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
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID | None = None, params: SortPageParams
) -> tuple[list[SalesOffer], str | None, int, bool]:
    stmt = select(SalesOffer).where(SalesOffer.tenant_id == tenant_id)
    if customer_id is not None:
        stmt = stmt.where(SalesOffer.customer_id == customer_id)
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesOffer, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
