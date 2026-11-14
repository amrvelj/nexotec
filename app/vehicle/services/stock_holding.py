"""Vehicle's projection of the records a Betrieb holds in stock (KAN-667,
ADR-131): what Vehicle knows of Stock's cars from Stock's own facts, and the
one question it answers — does the caller's Betrieb hold this record in
stock? — for the identity PATCH (a VIN change on such a car goes through
«VIN korrigieren», KAN-557) and the Vehicle 360's detail read.

Fed by two facts Stock publishes through the outbox (`CONSUMERS`, registered
in app/worker.py) and by the backfill (`apply_backfilled_holding`) for items
older than the consumers. Every write is a merge that is order-free and
idempotent: the record, the stock number and the moment the item left stock
are each set once (only a vehicle merge moves the record, KAN-524), so a
redelivered or overtaken fact changes nothing
(models/stock_holding.py says why these two facts are the whole lifecycle).

**Only the caller's own Betrieb counts** (rule 7). Every read names the
tenant from the token; another Betrieb's stock never changes the answer, the
refusal or the read — naming it would confirm that Betrieb holds the car.

A projection is late by the outbox's delay, and the PATCH it serves would
split a car in that window (the stock item keeps the old VIN). So the read
counts, besides the projection, **the facts Stock committed that Vehicle's
consumer has not applied** (the KAN-630 pattern, ADR-145): an
`inventory.pipeline_vehicle.vin_assigned` outbox row with no processed-event
row of `vehicle.stock_holding_on_vin_assigned` that is not yet published —
pending, or dead-lettered: it stays in flight until it is replayed, and the
VIN stays refused (fail safe). It is read before the projection: a consumer
commits the projection row and its processed-event row together, so a fact
the outbox read sees as applied is visible to the projection read after it,
and a fact applied between the two reads is still in flight for the first.
The reverse order lets one escape both. A published fact the consumer never
processed predates the consumers: the backfill covers it (run once per
environment after the deploy), and the nightly check «stock item missing from
vehicle_stock_holding» names every car it has not covered.
"""

import dataclasses
import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.processed_event_model import ProcessedEvent
from app.core.uuid7 import uuid7
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.services.merge_chain import records_living_on

VIN_ASSIGNED = "inventory.pipeline_vehicle.vin_assigned"
SOLD = "inventory.stock_item.sold"

# Event type -> Vehicle's consumer of it (app/worker.py registers exactly these).
CONSUMERS: dict[str, str] = {
    VIN_ASSIGNED: "vehicle.stock_holding_on_vin_assigned",
    SOLD: "vehicle.stock_holding_on_sold",
}

# The refusal's `details.reason` (utils/apiRefusal.ts translates it).
VIN_CHANGE_NEEDS_VIN_CORRECTION = "vin_change_needs_vin_correction"


@dataclass(frozen=True)
class StockHoldingFact:
    """One stock item as a fact or the backfill states it. None means the
    source does not say."""

    tenant_id: uuid.UUID
    stock_item_id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    stock_number: str | None = None
    left_stock_at: dt.datetime | None = None


@dataclass(frozen=True)
class HeldStockItem:
    """The stock item of the caller's Betrieb that holds a record — what the
    refusal and the Vehicle 360 link name. `stock_number` is the label, null
    when no fact has carried it yet."""

    stock_item_id: uuid.UUID
    stock_number: str | None


def apply_stock_holding_fact(db: Session, fact: StockHoldingFact, *, observed_at: dt.datetime) -> bool:
    """Merges `fact` into the projection, commit-free (the consumer harness
    or the backfill commits). Returns whether the row changed.

    The row is created with an `INSERT … ON CONFLICT DO NOTHING` and then
    locked, so two workers applying two facts of one item at once serialise
    on the row instead of one failing on the unique constraint."""

    from app.vehicle.services.merge import resolve_merged_id

    if fact.vehicle_id is not None:
        # KAN-524: a fact Stock wrote before a merge it had not seen names the
        # duplicate; the hold belongs to the car, which lives on the survivor
        # now — as the merge moved the rows already held.
        fact = dataclasses.replace(fact, vehicle_id=resolve_merged_id(db, fact.vehicle_id))
    insert = postgresql_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    now = utcnow()
    inserted = db.execute(
        insert(VehicleStockHolding)
        .values(
            id=uuid7(),
            tenant_id=fact.tenant_id,
            stock_item_id=fact.stock_item_id,
            stock_number=fact.stock_number,
            stock_number_refreshed_at=observed_at if fact.stock_number is not None else None,
            vehicle_id=fact.vehicle_id,
            left_stock_at=fact.left_stock_at,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "stock_item_id"])
        .returning(VehicleStockHolding.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return True

    row = db.scalars(
        select(VehicleStockHolding)
        .where(
            VehicleStockHolding.tenant_id == fact.tenant_id,
            VehicleStockHolding.stock_item_id == fact.stock_item_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    changed = False
    if fact.vehicle_id is not None and row.vehicle_id is None:
        row.vehicle_id = fact.vehicle_id
        changed = True
    if fact.left_stock_at is not None and row.left_stock_at is None:
        row.left_stock_at = fact.left_stock_at
        changed = True
    if fact.stock_number is not None and (
        row.stock_number_refreshed_at is None or observed_at > row.stock_number_refreshed_at
    ):
        changed = changed or row.stock_number != fact.stock_number
        row.stock_number = fact.stock_number
        row.stock_number_refreshed_at = observed_at
    if changed:
        row.updated_at = now
    db.flush()
    return changed


def apply_stock_holding_message(db: Session, message: OutboxMessage) -> None:
    """One of Stock's two facts, as its consumer receives it. The stock item
    is the message's aggregate, its Betrieb the message's `tenant_id`."""

    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    payload = message.payload or {}
    if message.event_type == VIN_ASSIGNED:
        vehicle_id = payload.get("vehicleId")
        if not vehicle_id:
            raise ValueError(f"{VIN_ASSIGNED} message {message.id} names no vehicleId.")
        fact = StockHoldingFact(
            tenant_id=message.tenant_id,
            stock_item_id=message.aggregate_id,
            vehicle_id=uuid.UUID(vehicle_id),
            stock_number=payload.get("stockNumber"),
        )
    elif message.event_type == SOLD:
        fact = StockHoldingFact(
            tenant_id=message.tenant_id, stock_item_id=message.aggregate_id, left_stock_at=message.occurred_at
        )
    else:
        raise ValueError(f"{message.event_type} is not a stock-holding fact.")
    apply_stock_holding_fact(db, fact, observed_at=message.occurred_at)


# --- what the PATCH and the detail read ask -----------------------------------------


def held_stock_item(db: Session, *, tenant_id: uuid.UUID | None, vehicle_id: uuid.UUID) -> HeldStockItem | None:
    """The stock item of `tenant_id` — the caller's Betrieb, from the token —
    that holds `vehicle_id`: held by the projection, or assigned by Stock in a
    fact the consumer has not applied yet (module docstring); else None. No
    other Betrieb's item is ever read. A fact for an item the projection
    already knows has left stock never counts.

    KAN-706 (ADR-160 amended): the merge re-points the holds it finds and a
    later fact lands on the live record (KAN-524), but an unapplied fact in the
    outbox still names the record Stock booked. One physical car: a hold or a
    fact on any record merged into `vehicle_id` (`records_living_on`, chains
    included) counts for it, so the survivor's VIN change is refused as the
    duplicate's would have been."""

    if tenant_id is None:
        return None
    vehicle_ids = records_living_on(db, vehicle_id)

    # Outbox first, projection second — the order is the safety (module
    # docstring): a fact applied between the two reads is either still in
    # flight for the first or visible to the second.
    left = select(VehicleStockHolding.id).where(
        VehicleStockHolding.tenant_id == tenant_id,
        VehicleStockHolding.stock_item_id == OutboxMessage.aggregate_id,
        VehicleStockHolding.left_stock_at.is_not(None),
    )
    applied = exists().where(
        ProcessedEvent.event_id == OutboxMessage.id,
        ProcessedEvent.consumer_name == CONSUMERS[VIN_ASSIGNED],
    )
    in_flight = db.scalars(
        select(OutboxMessage)
        .where(
            OutboxMessage.event_type == VIN_ASSIGNED,
            OutboxMessage.tenant_id == tenant_id,
            OutboxMessage.status != OutboxStatus.PUBLISHED,
            OutboxMessage.payload["vehicleId"].as_string().in_([str(record_id) for record_id in vehicle_ids]),
            ~applied,
            ~left.exists(),
        )
        .order_by(OutboxMessage.occurred_at)
        .limit(1)
    ).first()

    held = db.execute(
        select(VehicleStockHolding.stock_item_id, VehicleStockHolding.stock_number)
        .where(
            VehicleStockHolding.tenant_id == tenant_id,
            VehicleStockHolding.vehicle_id.in_(vehicle_ids),
            VehicleStockHolding.left_stock_at.is_(None),
        )
        .order_by(VehicleStockHolding.created_at)
        .limit(1)
    ).first()
    if held is not None:
        return HeldStockItem(stock_item_id=held.stock_item_id, stock_number=held.stock_number)
    if in_flight is None:
        return None
    return HeldStockItem(stock_item_id=in_flight.aggregate_id, stock_number=(in_flight.payload or {}).get("stockNumber"))


# --- the backfill (scripts/backfill_vehicle_stock_holding.py) -----------------------


def apply_backfilled_holding(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    stock_number: str,
    vehicle_id: uuid.UUID,
    read_at: dt.datetime,
) -> bool:
    """One stock item Stock holds with its record, as Stock answered when the
    backfill read it — merged as any fact is, so a fact consumed meanwhile (a
    sale) is never undone by the older read. Commit-free."""

    return apply_stock_holding_fact(
        db,
        StockHoldingFact(
            tenant_id=tenant_id, stock_item_id=stock_item_id, vehicle_id=vehicle_id, stock_number=stock_number
        ),
        observed_at=read_at,
    )
