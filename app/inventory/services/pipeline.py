"""Pipeline vehicles and promotion (WP-7 PR-2, ADR-045).

Two Sales auto-create paths, both idempotent, both landing in `pipeline`:
a manual configuration on contract confirmation, and a trade-in. Neither
corresponds to anything real in app.sales today — `grep -rn "contract"
app/sales` turns up nothing domain-related, only a pre-PRD-Sales-v2
Transaction model that ADR-050 will supersede in WP-8, and sales emits no
outbox events at all yet. `handle_sales_contract_confirmed` is built as
genuinely forward-compatible, idempotent consumer infrastructure — a
webhook handler built before its sender exists — against an OPAQUE
`contractId: GUID` and a synthetic payload shape documented below, tested
via directly-constructed events (tests/test_inventory_pipeline_consumer.
py), never coupled to app.sales.models.transaction.Transaction.

Expected future `sales.contract.confirmed` payload shape (not yet
produced anywhere):

    {
        "contractId": "<uuid>",
        "vehicleSource": "manual" | "existing",
        "manualConfiguration": {"vehicleLabel": str, "condition": str} | null,
        "tradeIn": {"vehicleLabel": str, "condition": str} | null,
        "pricingSnapshot": {"currency": "CHF", "basePrice": str|null, ...},
    }

`pricingSnapshot` (WP-8, ADR-046) is the frozen price build-up, added for
the WP-9 invoice leg; this consumer ignores it. It never carries margin,
trade-in purchase price or cost basis (ADR-029).

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
from app.inventory.services.stock_item import _build_and_flush_stock_item, mark_purchased_if_ready
from app.vehicle.public import create_or_get_vehicle_mdm

_EVENT_PRODUCER = "inventory"


def _create_pipeline_item_idempotent(
    db: Session, *, tenant_id: uuid.UUID, vehicle_label: str, condition: StockItemCondition, pipeline_ref: str
) -> StockItem:
    """Defense-in-depth against a genuine duplicate emission (a different
    message id, same business event) — the outbox harness's ProcessedEvent
    table already stops the SAME message id being handled twice; this
    catches the case that slips past it, via the (tenant_id, pipeline_ref)
    unique index.
    """

    existing = db.scalar(
        select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref)
    )
    if existing is not None:
        return existing

    try:
        # No commit here — this must land in the SAME transaction as the
        # outbox consumer harness's ProcessedEvent row (app.core.consumer's
        # own "one rule"). consume_once() commits once, after the handler
        # returns.
        return _build_and_flush_stock_item(
            db,
            tenant_id=tenant_id,
            data=StockItemCreate(vehicle_label=vehicle_label, condition=condition),
            actor_id=None,
            pipeline_ref=pipeline_ref,
        )
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.pipeline_ref == pipeline_ref)
        )
        if existing is None:
            raise
        return existing


def handle_sales_contract_confirmed(db: Session, *, tenant_id: uuid.UUID, payload: dict[str, Any]) -> None:
    contract_id = payload["contractId"]

    manual_configuration = payload.get("manualConfiguration")
    if manual_configuration is not None:
        _create_pipeline_item_idempotent(
            db,
            tenant_id=tenant_id,
            vehicle_label=manual_configuration["vehicleLabel"],
            condition=StockItemCondition(manual_configuration.get("condition", "new")),
            pipeline_ref=f"contract:{contract_id}:manual",
        )

    trade_in = payload.get("tradeIn")
    if trade_in is not None:
        _create_pipeline_item_idempotent(
            db,
            tenant_id=tenant_id,
            vehicle_label=trade_in["vehicleLabel"],
            condition=StockItemCondition(trade_in.get("condition", "used")),
            pipeline_ref=f"contract:{contract_id}:trade_in",
        )


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
