"""StockItem service layer (WP-7 PR-1)."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.inventory.models.stock_item import (
    AgeingBucket,
    LifecycleStatus,
    StockItem,
    StockItemCondition,
    StockNumberSequence,
)
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate
from app.vehicle.public import CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD, get_configuration_for_host

_EVENT_PRODUCER = "inventory"


def compute_ageing_bucket(item: StockItem) -> AgeingBucket | None:
    """None while the item has never been in_stock (pipeline) — ageing is
    "days on the lot," not "days since this row was created" (a factory
    order's time in the pipeline doesn't count).
    """

    if item.in_stock_at is None:
        return None
    days = (utcnow() - item.in_stock_at).days
    if days <= 60:
        return AgeingBucket.GREEN
    if days <= 120:
        return AgeingBucket.AMBER
    return AgeingBucket.RED


def allocate_stock_number(db: Session, tenant_id: uuid.UUID) -> str:
    """Row-lock-then-increment, one counter per tenant — same idiom as
    app.customer's per-group CustomerNumberSequence and app.vehicle's
    global VehicleNumberSequence, scoped to the dealership here because a
    stock number is this dealership's own stock, not a group or global
    fact.
    """

    row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(StockNumberSequence(tenant_id=tenant_id, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(StockNumberSequence, tenant_id, with_for_update=True)
        assert row is not None, "StockNumberSequence row missing after its first-use INSERT or the concurrent winner's"

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


def _build_and_flush_stock_item(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    data: StockItemCreate,
    actor_id: uuid.UUID | None,
    pipeline_ref: str | None,
    origin: dict[str, str] | None = None,
    configuration_label: str | None = None,
) -> StockItem:
    """The commit-free core, shared by create_stock_item (HTTP path, owns
    its own commit) and app.inventory.services.pipeline's consumer path
    (WP-7 PR-2, whose commit must be the SAME transaction as the outbox
    consumer harness's ProcessedEvent row — app.core.consumer's own "one
    rule": the side effect and its processed_event row are written
    together, or not at all).
    """

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
        pipeline_ref=pipeline_ref,
        configuration_id=data.configuration_id,
        configuration_label=configuration_label if data.configuration_id is not None else None,
        configuration_label_refreshed_at=utcnow() if data.configuration_id is not None else None,
        # A stock item created directly (not via the pipeline/promotion
        # path, PR-2) already has a VIN in hand — it goes straight to
        # in_stock rather than sitting in pipeline with nothing to promote.
        lifecycle_status=LifecycleStatus.IN_STOCK if data.vin else LifecycleStatus.PIPELINE,
        in_stock_at=utcnow() if data.vin else None,
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
            # KAN-144 — `origin` (originContractId, originRole) is added only
            # when a contract's confirmation created the item, so Sales can
            # link a manual configuration to it. Additive: a consumer that
            # ignores unknown keys is unaffected.
            payload={"stockNumber": item.stock_number, "vehicleLabel": item.vehicle_label, **(origin or {})},
        ),
    )
    return item


def mark_purchased_if_ready(db: Session, item: StockItem) -> bool:
    """WP-7 PR-5 (ADR-052): the dealership cannot invoice a vehicle it has
    not bought (Anto, 2026-10-04 — the gate is at invoicing, not at contract
    confirmation; KAN-100). A stock item becomes invoiceable the moment
    BOTH facts are true — VIN known (lifecycle_status=in_stock) and the
    purchase is booked (purchase_price set) — whichever of the two
    completes second. Called from both promote_to_vehicle_mdm (PR-2, VIN
    arriving after purchase was already booked) and record_purchase (PR-3,
    purchase booked after VIN already arrived). Idempotent: a no-op past
    the first time. Returns True iff it just flipped is_invoiceable and
    emitted the event — callers decide whether to commit.
    """

    if item.is_invoiceable:
        return False
    if item.lifecycle_status != LifecycleStatus.IN_STOCK or item.purchase_price is None:
        return False

    item.is_invoiceable = True
    db.flush()
    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.purchased",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"stockNumber": item.stock_number},
        ),
    )
    return True


def create_stock_item(
    db: Session, *, tenant_id: uuid.UUID, data: StockItemCreate, actor_id: uuid.UUID | None
) -> StockItem:
    configuration_label = None
    if data.configuration_id is not None:
        # FR-C-13: both modes are legitimate here — `build` for a factory
        # order, `record` for a car being bought in. Keeping `record` is what
        # makes the offer workspace's `build`-only rule safe: a used car
        # bought but not yet received is this pipeline item, which Path A
        # finds. 404 for another tenant's configuration.
        configuration = get_configuration_for_host(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
        configuration.require_mode(
            host="stock pipeline", allowed=(CONFIGURATION_MODE_BUILD, CONFIGURATION_MODE_RECORD)
        )
        configuration_label = configuration.label
    item = _build_and_flush_stock_item(
        db, tenant_id=tenant_id, data=data, actor_id=actor_id, pipeline_ref=None,
        configuration_label=configuration_label,
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
    # FR-I-12 (PR-5): a sold (invoiced) item is absent from the active
    # list — enforced here, never a 4th lifecycle_status value. Still
    # directly fetchable by id (get_stock_item_or_404 is unaffected) for
    # whoever holds a historical link to it.
    stmt = select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.left_stock_at.is_(None))
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
