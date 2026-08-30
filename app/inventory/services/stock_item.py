"""StockItem service layer (WP-7 PR-1)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition, StockNumberSequence
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate

_EVENT_PRODUCER = "inventory"


def allocate_stock_number(db: Session, tenant_id: uuid.UUID) -> str:
    """Row-lock-then-increment, one counter per tenant — same idiom as
    app.customer's per-group CustomerNumberSequence and app.vehicle's
    global VehicleNumberSequence, scoped to the dealership here because a
    stock number is this dealership's own stock, not a group or global
    fact.
    """

    row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        row = StockNumberSequence(tenant_id=tenant_id, next_value=1)
        db.add(row)
        db.flush()
        row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
        assert row is not None, "just-flushed StockNumberSequence row vanished before it could be re-read"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"S-{value:06d}"


def get_stock_item_or_404(db: Session, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> StockItem:
    item = db.scalar(
        select(StockItem).where(StockItem.id == stock_item_id, StockItem.tenant_id == tenant_id)
    )
    if item is None:
        raise NotFoundError(f"Stock item {stock_item_id} was not found.")
    return item


def create_stock_item(
    db: Session, *, tenant_id: uuid.UUID, data: StockItemCreate, actor_id: uuid.UUID | None
) -> StockItem:
    item = StockItem(
        tenant_id=tenant_id,
        stock_number=allocate_stock_number(db, tenant_id),
        vehicle_id=data.vehicle_id,
        vin=data.vin,
        vehicle_label=data.vehicle_label,
        condition=data.condition,
        location_id=data.location_id,
        odometer_km=data.odometer_km,
        list_price=data.list_price,
        effective_price=data.effective_price,
        first_registration_date=data.first_registration_date,
        # A stock item created directly (not via the pipeline/promotion
        # path, PR-2) already has a VIN in hand — it goes straight to
        # in_stock rather than sitting in pipeline with nothing to promote.
        lifecycle_status=LifecycleStatus.IN_STOCK if data.vin else LifecycleStatus.PIPELINE,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(item)
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.added",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"stockNumber": item.stock_number, "vehicleLabel": item.vehicle_label},
        ),
    )
    db.commit()
    db.refresh(item)
    return item


def update_stock_item(
    db: Session, *, item: StockItem, data: StockItemUpdate, actor_id: uuid.UUID | None
) -> StockItem:
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_by = actor_id
    item.version += 1
    db.flush()
    db.commit()
    db.refresh(item)
    return item


def change_condition(
    db: Session, *, item: StockItem, condition: StockItemCondition, actor_id: uuid.UUID | None
) -> StockItem:
    previous = item.condition
    item.condition = condition
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    if previous != condition:
        publish(
            db,
            OutboxEvent(
                event_type="inventory.stock_item.condition_changed",
                tenant_id=item.tenant_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="stock_item",
                aggregate_id=item.id,
                payload={"previousCondition": previous.value, "condition": condition.value},
            ),
        )
    db.commit()
    db.refresh(item)
    return item


def list_stock_items(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    q: str | None,
    lifecycle_status: LifecycleStatus | None,
    params: SortPageParams,
) -> tuple[list[StockItem], str | None, int, bool]:
    stmt = select(StockItem).where(StockItem.tenant_id == tenant_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (StockItem.stock_number.ilike(like))
            | (StockItem.vin.ilike(like))
            | (StockItem.vehicle_label.ilike(like))
        )
    if lifecycle_status is not None:
        stmt = stmt.where(StockItem.lifecycle_status == lifecycle_status)

    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=StockItem, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
