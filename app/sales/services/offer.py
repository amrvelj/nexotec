"""SalesOffer service layer (WP-8 PR-1). Creation and the container-based
autosave/completeness mechanics are split deliberately: PR-1 ships the bare
entity + grid; PR-2 adds `update_offer`'s container-completeness contract.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import Customer, CustomerLifecycleStatus, get_customer_or_404, resolve_live_customer_id
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.offer import OfferUpdate
from app.sales.services import ownership
from app.sales.services.configuration_reference import publish_held_configurations
from app.sales.services.deal_editing import apply_vehicle_and_pricing_changes
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_offer_number
from app.sales.services.priced_deal import PricedDeal, compute_containers, require_editable, require_no_live_contract
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot
from app.vehicle.public import HostConfiguration, copy_configuration_for_host, discard_configuration_copy

_EVENT_PRODUCER = "sales"

logger = logging.getLogger(__name__)


def resolve_customer_label(customer: Customer) -> str:
    """Shared with contract.py's own customer_id-sourced denormalization
    (KAN-58) — one label rule for both "attach a customer to an offer" and
    "create a contract directly for a customer"."""

    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def refuse_unattachable_customer(db: Session, *, customer: Customer, group_id: uuid.UUID, host: str) -> None:
    """The buyer rules an offer and a contract share, on create and on every
    change of buyer: a do-not-contact customer is refused (ADR-065/S-D19),
    and so is a merged one (KAN-656) — its record no longer exists as a
    working record, and an attach after `sales.customer_merged` moved the
    deals to the survivor would leave this deal behind on the duplicate.
    The refusal names the live customer the merge chain ends at, as an
    allocation's does (KAN-365), so the screen can offer it. `host` ends
    the sentence ("an offer", "a contract")."""

    # ADR-065/S-D19 — do-not-contact stops BOTH the offer and the
    # contract (it's about contact, not credit); a credit block is
    # the opposite case and deliberately does NOT stop here — it
    # only stops confirm_contract (S-D19: "quoting a blocked
    # customer is often how the block gets resolved").
    if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
        raise ConflictError(
            f"Customer {customer.customer_number} is do-not-contact — cannot be attached to {host}.",
            details={"reason": "customer_do_not_contact", "customerNumber": customer.customer_number},
        )
    if customer.lifecycle_status == CustomerLifecycleStatus.MERGED:
        survivor_id = resolve_live_customer_id(db, group_id=group_id, customer_id=customer.id)
        raise ConflictError(
            f"Customer {customer.customer_number} has been merged into another — cannot be attached to {host}; "
            "attach the customer it was merged into.",
            details={
                "reason": "customer_merged",
                "mergedIntoCustomerId": str(survivor_id) if survivor_id else None,
            },
        )


def apply_customer_change(
    db: Session, *, deal: PricedDeal, group_id: uuid.UUID, customer_id: uuid.UUID | None, host: str
) -> None:
    """Sets or clears the buyer of an offer draft or a pending contract —
    one rule for both (the contract's since KAN-307, Anto 2026-10-12:
    "the same as offer generation"; ADR-083 amended). `customer_id` is
    resolved server-side into the denormalized label and correspondence
    language on every change (never trusted from the client). A
    do-not-contact or merged customer is refused
    (`refuse_unattachable_customer`); `host` ends its sentence. The caller has
    checked the deal is editable and owns the transaction."""

    if customer_id is None:
        deal.customer_id = None
        deal.customer_label = None
        deal.customer_language = None
        deal.customer_denorm_refreshed_at = None
        return
    customer = get_customer_or_404(db, group_id, customer_id)
    refuse_unattachable_customer(db, customer=customer, group_id=group_id, host=host)
    deal.customer_id = customer.id
    deal.customer_label = resolve_customer_label(customer)
    # CLAUDE.md's own rule: "the customer's correspondence language
    # is not the user's UI language" — denormalized here so
    # services/document.py never has to reach back into customer
    # at generation time.
    deal.customer_language = customer.language.value
    # customer_locality is written by no path, here or anywhere else, and
    # stays as it is (Anto, 2026-11-10, KAN-590: neither filled nor dropped).
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
        # KAN-200: the creator is the responsible seller (when they are a
        # user of this dealership); a manager can hand the offer on later.
        **ownership.default_owner(db, tenant_id=tenant_id, actor_id=actor_id),
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

    An edit publishes no event, except that an offer starting to hold a
    configuration id (Path B through deal_editing) publishes
    `sales.offer.configuration_attached` in this transaction, so Vehicle
    keeps a configuration a host took (KAN-270, `.claude/rules/sales.md`).
    """

    changes = data.model_dump(exclude_unset=True)
    if changes.keys() == {"owner_user_id"} and offer.status == OfferStatus.OPEN:
        return _change_seller_only(db, offer=offer, owner_user_id=changes["owner_user_id"], actor_id=actor_id)

    require_editable(db, offer)

    if "customer_id" in changes:
        apply_customer_change(db, deal=offer, group_id=group_id, customer_id=changes.pop("customer_id"), host="an offer")
    if "owner_user_id" in changes:
        # KAN-200: the responsible seller, re-assignable from the workspace.
        ownership.apply_owner(
            offer, ownership.resolve_owner_change(db, tenant_id=offer.tenant_id, owner_user_id=changes.pop("owner_user_id"))
        )

    apply_vehicle_and_pricing_changes(db, deal=offer, changes=changes, actor_id=actor_id, host="offer workspace")

    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def _change_seller_only(
    db: Session, *, offer: SalesOffer, owner_user_id: uuid.UUID | None, actor_id: uuid.UUID | None
) -> SalesOffer:
    """KAN-405 (Anto, 2026-10-28, option A): an offer sent to the customer is
    read-only, but its responsible seller may still change — a PATCH of
    `ownerUserId` alone, so a deal in the unassigned queue can be taken over.
    The seller is not on the sent document; no price figure is touched and
    the snapshot is not re-frozen. An offer with a live contract still
    refuses (`offer_has_contract`): the contract carries the seller then."""

    require_no_live_contract(db, offer)
    ownership.apply_owner(offer, ownership.resolve_owner_change(db, tenant_id=offer.tenant_id, owner_user_id=owner_user_id))
    offer.updated_by = actor_id
    offer.version += 1
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def _copy_configuration(db: Session, *, source: SalesOffer, actor_id: uuid.UUID | None) -> HostConfiguration | None:
    """The source offer's configuration, copied and committed by vehicle on a
    session of its own (ADR-047) — never the request's, whose pending work
    vehicle's commit would land."""

    if source.configuration_id is None:
        return None
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        return copy_configuration_for_host(
            own, tenant_id=source.tenant_id, configuration_id=source.configuration_id, actor_id=actor_id
        )


def _discard_configuration(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    source_offer_id: uuid.UUID,
    configuration_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """The compensation of `_copy_configuration` after the copy offer's write
    failed. A failing discard leaves an unreferenced configuration (KAN-270's
    territory) and must not hide the original error, so it is logged."""

    try:
        with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
            discard_configuration_copy(own, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=actor_id)
    except Exception:
        logger.exception(
            "copied configuration not discarded after a failed offer copy",
            extra={"configuration_id": str(configuration_id), "source_offer_id": str(source_offer_id)},
        )


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

    FR-C-11 (KAN-188): a configured car gets a configuration of its own,
    copied by vehicle on a session of its own before the offer is written
    (ADR-047), so reopening the configurator on the copy never edits the
    source's configuration — nor the pipeline item a contract from the
    source points at. If the offer's own write fails, the copy is discarded
    again (the compensating action).
    """

    tenant_id, source_offer_id = source.tenant_id, source.id
    configuration = _copy_configuration(db, source=source, actor_id=actor_id)
    try:
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
            stock_item_label=source.stock_item_label,
            stock_item_denorm_refreshed_at=source.stock_item_denorm_refreshed_at,
            vehicle_label=source.vehicle_label,
            manual_vehicle_condition=source.manual_vehicle_condition,
            manual_base_price=source.manual_base_price,
            # C-F (KAN-10): a configured car stays configured on the copy, re-frozen
            # below like everything else — on a configuration of its own (KAN-188).
            configuration_id=configuration.id if configuration is not None else None,
            configuration_label=configuration.label if configuration is not None else None,
            configuration_label_refreshed_at=utcnow() if configuration is not None else None,
            discount_type=source.discount_type,
            discount_value=source.discount_value,
            trade_in_vehicle_id=source.trade_in_vehicle_id,
            trade_in_label=source.trade_in_label,
            trade_in_vin=source.trade_in_vin,
            trade_in_valuation_id=source.trade_in_valuation_id,
            # KAN-188, Anto's ruling 2026-10-28 (option A): the trade-in keeps the
            # configuration its valuation was judged on — a copy of its own would
            # disagree with the valuation the copy still carries.
            trade_in_configuration_id=source.trade_in_configuration_id,
            trade_in_configuration_label_refreshed_at=source.trade_in_configuration_label_refreshed_at,
            trade_in_value=source.trade_in_value,
            trade_in_purchase_price=source.trade_in_purchase_price,
            leasing_down_payment=source.leasing_down_payment,
            leasing_term_months=source.leasing_term_months,
            leasing_km_per_year=source.leasing_km_per_year,
            # KAN-200: a copy is the copier's own offer, not the source seller's.
            **ownership.default_owner(db, tenant_id=source.tenant_id, actor_id=actor_id),
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
        # KAN-270: the copy holds its own configuration and the source's
        # trade-in one; vehicle keeps a configuration a host took for good.
        publish_held_configurations(db, deal=offer)
        upsert_deal_projection(db, offer=offer)
        db.commit()
    except Exception:
        db.rollback()
        if configuration is not None:
            _discard_configuration(
                db, tenant_id=tenant_id, source_offer_id=source_offer_id, configuration_id=configuration.id,
                actor_id=actor_id,
            )
        raise
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
    require_no_live_contract(db, offer)  # KAN-408

    missing = [c.id for c in compute_containers(offer) if c.requirement == "required" and c.status != "complete"]
    if missing:
        raise ConflictError(
            f"Offer {offer.offer_number} is missing required containers: {', '.join(missing)}.",
            details={"reason": "missing_containers", "missingContainers": missing},
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
    # KAN-408: an offer with a live contract is closed by cancelling the
    # contract, which frees it; cancelling the offer under it is refused.
    require_no_live_contract(db, offer)

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
