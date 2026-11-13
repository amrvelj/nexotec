"""Pipeline vehicles and promotion (WP-7 PR-2, ADR-045).

Two Sales auto-create paths, both idempotent, both landing in `pipeline`:
a manual configuration on contract confirmation, and a trade-in. The
sender is `app.sales.services.contract.confirm_contract`; this handler
reads its `sales.contract.confirmed` payload against an OPAQUE
`contractId: GUID`:

    {
        "contractId": "<uuid>",
        "vehicleSource": "manual" | "existing",
        "manualConfiguration": {"vehicleLabel": str, "condition": str} | null,
        "customerId": "<uuid>" | null,
        "signedAt": "<iso datetime>",
        "tradeIn": {"vehicleLabel": str, "condition": str, "valuationId"?: "<uuid>", "vin"?: str} | null,
        "pricingSnapshot": {"currency": "CHF", "basePrice": str|null, ...},
    }

`tradeIn.valuationId` (KAN-101) is present when the trade-in carries a
valuation; the trade-in's pipeline item then gets Stock's valuation
pointer, read from app.valuation.public in this same transaction.

`tradeIn.vin` (KAN-536, additive) is the VIN the contract holds for the
trade-in, when it holds one. The trade-in's pipeline item keeps it
(`trade_in_vin`) to prefill and check its purchase booking; a confirmation
published before KAN-536 has none, and the item is then bought as before.

`customerId` and `signedAt` (KAN-632) are kept on a trade-in's pipeline
item (`trade_in_customer_id`, `trade_in_signed_at`) and carried on by its
purchase and storno events, so the customer context names the former owner
without asking Sales; a confirmation published before KAN-632 has neither.

`pricingSnapshot` (WP-8, ADR-046) is the frozen price build-up, added for
the WP-9 invoice leg; this consumer ignores it. It never carries margin,
trade-in purchase price or cost basis (ADR-029).

The manual configuration's item is created reserved for the contract
(KAN-158, PRD-Stock K-12): the customer has ordered that car. The
trade-in's is not — the dealership is buying it, nobody has ordered it.

`vehicleSource == "manual"` and a non-empty `tradeIn` are independent —
a contract can carry either, both, or neither (a manual configuration
paid for partly by a trade-in is the ordinary case, not an edge case).
"""

import uuid
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.validators import NormalisedVin
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.configuration_holding import (
    configuration_holder,
    discard_copies,
    publish_reassigned,
    settle_ordered_configuration,
)
from app.inventory.services.reservation import contract_is_cancelled, lock_contract, reserve_and_flush
from app.inventory.services.stock_item import (
    CONFIGURATION_INDEX,
    _build_and_flush_stock_item,
    _check_vin_not_in_stock,
    _item_in_stock_with_vin,
    _vin_already_in_stock,
    apply_trade_in_seller,
    apply_trade_in_vin,
    configuration_held_conflict,
    mark_purchased_if_ready,
)
from app.inventory.services.valuation import apply_valuation_ref
from app.valuation.public import get_valuation_or_404
from app.vehicle.public import resolve_vehicle_mdm_by_vin

_EVENT_PRODUCER = "inventory"

# KAN-324: the create's own normalisation and rule (KAN-244, option A) — one
# spelling for the KAN-111 duplicate check, its index and vehicle-mdm.
_VIN = TypeAdapter(NormalisedVin)


def _normalised_vin(value: str) -> str:
    """Upper-cased, whitespace removed, then validated as vehicle-mdm
    validates a VIN — 422 `invalid_vin` otherwise, before anything is read
    or written."""

    try:
        return _VIN.validate_python(value)
    except ValidationError as exc:
        raise UnprocessableEntityError(
            "; ".join(str(error["msg"]) for error in exc.errors()), details={"reason": "invalid_vin"}
        ) from None


def _create_pipeline_item_idempotent(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    vehicle_label: str,
    condition: StockItemCondition,
    pipeline_ref: str,
    origin: dict[str, str],
    configuration_id: str | None = None,
) -> tuple[StockItem, bool]:
    """Returns the item and whether this call created it.

    Defense-in-depth against a genuine duplicate emission (a different
    message id, same business event) — the outbox harness's ProcessedEvent
    table already stops the SAME message id being handled twice; this
    catches the case that slips past it, via the (tenant_id, pipeline_ref)
    unique index.
    """

    existing = db.scalar(
        select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref)
    )
    if existing is not None:
        return existing, False

    try:
        # No commit here — this must land in the SAME transaction as the
        # outbox consumer harness's ProcessedEvent row (app.core.consumer's
        # own "one rule"). consume_once() commits once, after the handler
        # returns.
        item = _build_and_flush_stock_item(
            db,
            tenant_id=tenant_id,
            data=StockItemCreate(
                vehicle_label=vehicle_label,
                condition=condition,
                configuration_id=uuid.UUID(configuration_id) if configuration_id else None,
            ),
            actor_id=None,
            pipeline_ref=pipeline_ref,
            origin=origin,
            # C-F (KAN-10): the contract's vehicle label is the
            # configuration's label at the time it was attached.
            configuration_label=vehicle_label if configuration_id else None,
        )
        return item, True
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref)
        )
        if existing is None:
            # KAN-513: a writer that skipped the lock took the configuration in
            # between (none is known; defence in depth) — the same 409 as create
            # and attach.
            holder = (
                configuration_holder(db, tenant_id=tenant_id, configuration_id=uuid.UUID(configuration_id))
                if configuration_id and CONFIGURATION_INDEX in str(exc.orig)
                else None
            )
            if holder is not None:
                raise configuration_held_conflict(holder) from None
            raise
        return existing, False


def _item_for_ref(db: Session, *, tenant_id: uuid.UUID, pipeline_ref: str) -> StockItem | None:
    return db.scalar(select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref))


def handle_sales_contract_confirmed(db: Session, *, tenant_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """FR-I-02/02b. KAN-513: a configuration copy made on the way (its own
    session, ADR-047) is discarded again if the rest of the handler fails —
    the compensating action; `configuration_holding`'s docstring says what
    a failed commit after it leaves."""

    copies: list[uuid.UUID] = []
    try:
        _handle_sales_contract_confirmed(db, tenant_id=tenant_id, payload=payload, copies=copies)
    except Exception:
        discard_copies(db, tenant_id=tenant_id, configuration_ids=copies)
        raise


def _handle_sales_contract_confirmed(
    db: Session, *, tenant_id: uuid.UUID, payload: dict[str, Any], copies: list[uuid.UUID]
) -> None:
    contract_id = payload["contractId"]

    manual_configuration = payload.get("manualConfiguration")
    if manual_configuration is not None:
        # KAN-158 — serialised with this contract's cancellation consumer.
        lock_contract(db, tenant_id=tenant_id, contract_id=uuid.UUID(contract_id))
        pipeline_ref = f"contract:{contract_id}:manual"
        configuration_id = manual_configuration.get("configurationId")
        reused: StockItem | None = None
        if configuration_id and _item_for_ref(db, tenant_id=tenant_id, pipeline_ref=pipeline_ref) is None:
            # KAN-513 (Anto, 2026-11-03, option A): a configuration a stock
            # item already holds — a second contract on the same offer — reuses
            # that car while it is free on stock, else the order gets a copy.
            settled = settle_ordered_configuration(
                db, tenant_id=tenant_id, configuration_id=uuid.UUID(configuration_id),
                contract_id=uuid.UUID(contract_id), may_reuse=True,
            )
            reused = settled.reuse
            configuration_id = str(settled.configuration_id)
            if settled.copied:
                copies.append(settled.configuration_id)
        if reused is not None:
            # The same reservation rule as a new item's (KAN-158), and the
            # fact Sales links the car by. A redelivery finds the car already
            # reserved for this contract and does nothing more.
            if reused.reserved_by_contract_id != uuid.UUID(contract_id) and not contract_is_cancelled(
                db, tenant_id=tenant_id, contract_id=uuid.UUID(contract_id)
            ):
                reserve_and_flush(db, item=reused, contract_id=uuid.UUID(contract_id))
                publish_reassigned(db, item=reused, contract_id=uuid.UUID(contract_id))
        else:
            ordered, created = _create_pipeline_item_idempotent(
                db,
                tenant_id=tenant_id,
                vehicle_label=manual_configuration["vehicleLabel"],
                condition=StockItemCondition(manual_configuration.get("condition", "new")),
                pipeline_ref=pipeline_ref,
                origin={"originContractId": str(contract_id), "originRole": "manual_configuration"},
                # C-F (KAN-10, FR-C-12) — the pipeline item carries the
                # configuration (or, KAN-513, its copy); no vehicle-mdm record
                # is written (ADR-070).
                configuration_id=configuration_id,
            )
            # KAN-158 (PRD-Stock K-12, FR-I-11) — the ordered car is reserved for
            # its contract from the moment it exists, in this same transaction.
            # Only an item this call creates (a duplicate emission that finds the
            # item already there leaves it as it is), and never for a contract
            # Stock already knows is cancelled: the cancellation can be consumed
            # before this confirmation, when the confirmation's first delivery
            # failed and is retried after backoff.
            if created and not contract_is_cancelled(db, tenant_id=tenant_id, contract_id=uuid.UUID(contract_id)):
                reserve_and_flush(db, item=ordered, contract_id=uuid.UUID(contract_id))

    trade_in = payload.get("tradeIn")
    if trade_in is not None:
        trade_in_ref = f"contract:{contract_id}:trade_in"
        trade_in_configuration_id = trade_in.get("configurationId")
        if trade_in_configuration_id and _item_for_ref(db, tenant_id=tenant_id, pipeline_ref=trade_in_ref) is None:
            # KAN-513: a trade-in's item is never reused for another contract;
            # a configuration another item holds (one valuation backing two
            # contracts) is copied for this one.
            settled_trade_in = settle_ordered_configuration(
                db, tenant_id=tenant_id, configuration_id=uuid.UUID(trade_in_configuration_id),
                contract_id=uuid.UUID(contract_id), may_reuse=False,
            )
            trade_in_configuration_id = str(settled_trade_in.configuration_id)
            if settled_trade_in.copied:
                copies.append(settled_trade_in.configuration_id)
        item, created = _create_pipeline_item_idempotent(
            db,
            tenant_id=tenant_id,
            vehicle_label=trade_in["vehicleLabel"],
            condition=StockItemCondition(trade_in.get("condition", "used")),
            pipeline_ref=trade_in_ref,
            origin={"originContractId": str(contract_id), "originRole": "trade_in"},
            configuration_id=trade_in_configuration_id,
        )
        if created:
            # KAN-632 — who traded the car in and when the contract was
            # signed, kept for the item's purchase and storno events
            # (`trade_in_origin`). A confirmation published before KAN-632
            # carries neither; the item then keeps none.
            apply_trade_in_seller(item, customer_id=payload.get("customerId"), signed_at=payload.get("signedAt"))
            # KAN-536 — the VIN the contract holds for the car, kept as the
            # item's known future VIN (`trade_in_vin`, never `vin`: no
            # vehicle-mdm record and no stock VIN before the purchase,
            # ADR-045). A confirmation published before KAN-536 carries none.
            apply_trade_in_vin(item, vin=trade_in.get("vin"))
            db.flush()
        valuation_id = trade_in.get("valuationId")
        if valuation_id is not None:
            # KAN-101 (ADR-048) — Stock holds the pointer, never a copy of
            # the valuation's inputs; the valuation module stays its writer.
            valuation = get_valuation_or_404(db, tenant_id, uuid.UUID(valuation_id))
            apply_valuation_ref(
                item,
                valuation_id=valuation.id,
                amount=valuation.final_offer,
                valued_at=valuation.created_at,
                source=valuation.source.value,
            )
            db.flush()


def promote_to_vehicle_mdm(
    db: Session, *, item: StockItem, vin: str, catalogue_variant_id: uuid.UUID | None = None
) -> StockItem:
    """FR-V-04 as its own unit of work: `promote_and_flush`, then commit.
    Purchase booking (KAN-446) promotes inside its own transaction instead."""

    item = promote_and_flush(db, item=item, vin=vin, catalogue_variant_id=catalogue_variant_id)
    db.commit()
    db.refresh(item)
    return item


def promote_and_flush(
    db: Session,
    *,
    item: StockItem,
    vin: str,
    catalogue_variant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    correct_vin: bool = False,
) -> StockItem:
    """FR-V-04: VIN arrival on a pipeline item. Idempotent per stock item:
    it returns early when the item already carries a `vehicle_id` for the
    same VIN, so a second call is a no-op, not a second event (KAN-127).
    The vehicle record keeps no pointer back to the stock entry: Stock's
    `stock_item.vehicle_id` is the one link between the two, and a car
    bought, sold and bought again has one stock entry per stay, each naming
    it (Anto, 2026-10-31, KAN-504 option A; PRD-Vehicles FR-V-04 amended).

    `vin` is normalised and validated first (KAN-324), as a create's is, so
    a retry spelling the same VIN differently is the same no-op, and a VIN
    still malformed after normalising is refused (422) before any read or
    write. It never commits: the caller's transaction does (KAN-446).
    """

    vin = _normalised_vin(vin)
    if item.vehicle_id is not None and item.vin == vin:
        return item  # already promoted — redelivery/retry, not a second event
    # Anything else that is not pipeline is refused — including, since KAN-244
    # gives a directly created in-stock item its vehicle, such an item asked
    # to "promote" to another VIN (it used to fall through the line above).
    if item.lifecycle_status != LifecycleStatus.PIPELINE:
        raise ConflictError(
            f"Stock item {item.id} is not pipeline (lifecycle_status={item.lifecycle_status.value}) — nothing to promote.",
            details={"stockItemId": str(item.id)},
        )

    # KAN-554 (ADR-116): a pipeline item created with its VIN is promoted
    # under that VIN only — another one describes another car — unless the
    # purchase booking corrects it to its configuration's (KAN-575,
    # `correct_vin`; the booking audits it). The savepoint below writes the
    # new VIN, so a racing holder is refused by the index as for any VIN.
    if item.vin is not None and item.vin != vin and not correct_vin:
        raise UnprocessableEntityError(
            f"Stock item {item.id} carries VIN {item.vin}, not {vin}.",
            details={"reason": "purchase_vin_mismatch", "stockItemId": str(item.id)},
        )

    # KAN-111: the same car may be in pipeline twice (two contracts can
    # carry one trade-in, KAN-101), never twice in stock. Checked before
    # vehicle-mdm is touched, so a refused promotion writes nothing. The item
    # itself is left out: one created with its VIN holds it already (KAN-554).
    _check_vin_not_in_stock(db, tenant_id=item.tenant_id, vin=vin, excluding=item.id)

    # On vehicle-mdm's own session (`vehicle.public.resolve_vehicle_mdm_by_vin`,
    # KAN-337): its
    # commit never commits this transaction, and losing its insert race to
    # another promotion of a VIN it had never seen (KAN-257) resolves to the
    # winner's record instead of rolling this session back. A winner that
    # took the car into this dealership's stock is refused by the index below.
    # KAN-243, KAN-554: a record purchase booking creates names the user who
    # booked it (`actor_id`); a promotion with none is a system write.
    vehicle = resolve_vehicle_mdm_by_vin(db, vin=vin, catalogue_variant_id=catalogue_variant_id, actor_id=actor_id)

    try:
        # A savepoint, so a racing promotion of the same VIN undoes only
        # this item's change (expiring it back to its pipeline state), not
        # the caller's session.
        with db.begin_nested():
            item.vehicle_id = vehicle.id
            item.vin = vehicle.vin
            item.lifecycle_status = LifecycleStatus.IN_STOCK
            item.in_stock_at = utcnow()
            item.version += 1
    except IntegrityError:
        held_by = _item_in_stock_with_vin(db, tenant_id=item.tenant_id, vin=vin, excluding=item.id)
        if held_by is None:
            raise
        raise _vin_already_in_stock(held_by) from None

    publish(
        db,
        OutboxEvent(
            event_type="inventory.pipeline_vehicle.vin_assigned",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"vin": vin, "vehicleId": str(vehicle.id)},
        ),
    )
    # WP-7 PR-5 (ADR-052) — an item whose purchase was booked before its VIN
    # arrived (possible until KAN-446, ADR-103) is invoiceable the moment it
    # is promoted. Since then purchase booking promotes first, and this is a
    # no-op there: the purchase facts are written after it.
    mark_purchased_if_ready(db, item)
    db.flush()
    return item
