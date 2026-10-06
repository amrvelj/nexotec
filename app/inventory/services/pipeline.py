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
        "tradeIn": {"vehicleLabel": str, "condition": str, "valuationId"?: "<uuid>"} | null,
        "pricingSnapshot": {"currency": "CHF", "basePrice": str|null, ...},
    }

`tradeIn.valuationId` (KAN-101) is present when the trade-in carries a
valuation; the trade-in's pipeline item then gets Stock's valuation
pointer, read from app.valuation.public in this same transaction.

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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.reservation import contract_is_cancelled, reserve_and_flush
from app.inventory.services.stock_item import _build_and_flush_stock_item, mark_purchased_if_ready
from app.inventory.services.valuation import apply_valuation_ref
from app.valuation.public import get_valuation_or_404
from app.vehicle.public import create_or_get_vehicle_mdm

_EVENT_PRODUCER = "inventory"


def _create_pipeline_item_idempotent(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    vehicle_label: str,
    condition: StockItemCondition,
    pipeline_ref: str,
    origin: dict[str, str],
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
            data=StockItemCreate(vehicle_label=vehicle_label, condition=condition),
            actor_id=None,
            pipeline_ref=pipeline_ref,
            origin=origin,
        )
        return item, True
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref)
        )
        if existing is None:
            raise
        return existing, False


def handle_sales_contract_confirmed(db: Session, *, tenant_id: uuid.UUID, payload: dict[str, Any]) -> None:
    contract_id = payload["contractId"]

    manual_configuration = payload.get("manualConfiguration")
    if manual_configuration is not None:
        ordered, created = _create_pipeline_item_idempotent(
            db,
            tenant_id=tenant_id,
            vehicle_label=manual_configuration["vehicleLabel"],
            condition=StockItemCondition(manual_configuration.get("condition", "new")),
            pipeline_ref=f"contract:{contract_id}:manual",
            origin={"originContractId": str(contract_id), "originRole": "manual_configuration"},
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
        item, _created = _create_pipeline_item_idempotent(
            db,
            tenant_id=tenant_id,
            vehicle_label=trade_in["vehicleLabel"],
            condition=StockItemCondition(trade_in.get("condition", "used")),
            pipeline_ref=f"contract:{contract_id}:trade_in",
            origin={"originContractId": str(contract_id), "originRole": "trade_in"},
        )
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
    """FR-V-04: VIN arrival on a pipeline item. Idempotent by
    `pipeline_vehicle_id` (= the stock item's own id) — a second call with
    the item already promoted is a no-op, not a second event.
    """

    if item.vehicle_id is not None:
        return item  # already promoted — redelivery/retry, not a second event
    if item.lifecycle_status != LifecycleStatus.PIPELINE:
        raise ConflictError(
            f"Stock item {item.id} is not pipeline (lifecycle_status={item.lifecycle_status.value}) — nothing to promote.",
            details={"stockItemId": str(item.id)},
        )

    vehicle, _created = create_or_get_vehicle_mdm(db, vin=vin, catalogue_variant_id=catalogue_variant_id)

    item.vehicle_id = vehicle.id
    item.vin = vehicle.vin
    item.lifecycle_status = LifecycleStatus.IN_STOCK
    item.in_stock_at = utcnow()
    item.version += 1
    db.flush()

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
    # WP-7 PR-5 (ADR-052) — a trade-in's purchase is sometimes booked
    # BEFORE its VIN arrives (FR-I-02b's "awaiting purchase booking" case
    # is the other order; this is the promotion-completes-second order).
    mark_purchased_if_ready(db, item)
    db.commit()
    db.refresh(item)
    return item
