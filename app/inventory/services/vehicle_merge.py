"""KAN-524 (FR-V-12; Anto, 2026-11-13 Q2 A, 2026-11-14 Q3 A and Q4 A): Stock's
items follow a vehicle-mdm merge.

Vehicle merges two records of one car in its own transaction and publishes
`vehicle.vehicle_mdm.merged` (`vehicle/services/merge.py`); it never calls
Stock (ADR-131). Stock's consumer `inventory.stock_items_on_vehicle_merged`
then moves every stock item naming the duplicate — every Betrieb's, sold rows
too, since the record a sold row names should resolve to its car without a
hop — onto the record the car lives on now (`resolve_merged_vehicle_id`: a
merge delivered after a later one lands on the live record).

A car still in stock also takes that record's VIN (Q3 A): the duplicate
usually came from a VIN typo, so the item still carries the typo. Its
configuration follows on Vehicle's own session first
(`vehicle.public.follow_vehicle_merge_on_configuration`, compare-and-set,
ADR-047), then the item is written here. A sold row keeps the VIN it was sold
under. One exception: when another live item of the same Betrieb already
holds the survivor's VIN or record — that Betrieb had the car in stock twice,
which the merge refuses only for the caller's own Betrieb (Q4 A, rule 7) — the
item moves and keeps its VIN, and inventory's nightly reconciliation reports
«two live stock items name one vehicle record» to that Betrieb, whose manager
cancels one.

Each moved item gets an audit row naming the merge (`vehicle_merged`, the
system actor) and its version bumped. Commit-free: the consumer harness commits
with the processed-event row, so a redelivery finds nothing left to move.
"""

import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.core.validators import normalise_vin
from app.inventory.models.stock_item import StockItem
from app.vehicle.public import follow_vehicle_merge_on_configuration, get_vehicle_summaries, resolve_merged_vehicle_id

logger = logging.getLogger(__name__)

CONSUMER_NAME = "inventory.stock_items_on_vehicle_merged"


def move_stock_items_on_vehicle_merge(db: Session, *, duplicate_vehicle_id: uuid.UUID, survivor_vehicle_id: uuid.UUID) -> int:
    """Moves every stock item naming `duplicate_vehicle_id` onto the record
    `survivor_vehicle_id` lives on now. Returns how many moved."""

    live_id = resolve_merged_vehicle_id(db, survivor_vehicle_id)
    summaries = get_vehicle_summaries(db, [live_id])
    survivor = summaries.get(live_id)
    if survivor is None:
        raise ValueError(f"vehicle.vehicle_mdm.merged names survivor {survivor_vehicle_id}, which is no record.")
    survivor_vin = normalise_vin(survivor.vin)

    items = list(
        db.scalars(
            select(StockItem)
            .where(StockItem.vehicle_id == duplicate_vehicle_id)
            .order_by(StockItem.id)
            .with_for_update()
        ).all()
    )
    for item in items:
        before = {"vehicleId": str(item.vehicle_id), "vin": item.vin}
        item_vin = normalise_vin(item.vin) if item.vin is not None else None
        takes_vin = (
            item.left_stock_at is None
            and item_vin is not None
            and item_vin != survivor_vin
            and not _betrieb_holds_the_survivor(db, item=item, survivor_id=live_id, survivor_vin=survivor_vin)
        )
        if takes_vin and item_vin is not None and item.configuration_id is not None:
            _configuration_follows(db, item, previous_vin=item_vin, vin=survivor_vin)
        item.vehicle_id = live_id
        if takes_vin:
            item.vin = survivor_vin
        item.version += 1
        record_audit_event(
            db,
            entity_type="stock_item",
            entity_id=item.id,
            tenant_id=item.tenant_id,
            action="vehicle_merged",
            actor_id=None,
            before=before,
            after={"vehicleId": str(live_id), "vin": item.vin, "mergedVehicleId": str(duplicate_vehicle_id)},
            reason="vehicle_merged",
        )
    db.flush()
    return len(items)


def _betrieb_holds_the_survivor(db: Session, *, item: StockItem, survivor_id: uuid.UUID, survivor_vin: str) -> bool:
    """Another live item of the item's Betrieb already holds the survivor's
    record or VIN (Q4 A's double booking): the item cannot take the VIN, which
    `uq_stock_item_tenant_id_iso_vin_in_stock` would refuse."""

    return (
        db.scalar(
            select(StockItem.id).where(
                StockItem.tenant_id == item.tenant_id,
                StockItem.id != item.id,
                StockItem.left_stock_at.is_(None),
                or_(StockItem.vehicle_id == survivor_id, StockItem.vin == survivor_vin),
            ).limit(1)
        )
        is not None
    )


def _configuration_follows(db: Session, item: StockItem, *, previous_vin: str, vin: str) -> None:
    """The item's configuration takes the survivor's VIN on Vehicle's own
    session. A configuration naming a third VIN describes yet another car: it
    is left as it is (422 there, nothing written), the item still takes the
    record's VIN, and inventory's reconciliation reports the configuration
    that differs (KAN-557's check)."""

    assert item.configuration_id is not None
    try:
        follow_vehicle_merge_on_configuration(
            db,
            tenant_id=item.tenant_id,
            configuration_id=item.configuration_id,
            previous_vin=previous_vin,
            vin=vin,
        )
    except UnprocessableEntityError:
        logger.warning(
            "stock item %s: configuration %s names a third VIN; left for reconciliation (KAN-524)",
            item.id,
            item.configuration_id,
        )


def handle_vehicle_merged_message(db: Session, message: OutboxMessage) -> None:
    """The consumer of `vehicle.vehicle_mdm.merged`
    (`inventory.stock_items_on_vehicle_merged`). Flushes only; consume_once
    commits it together with the processed_event row."""

    payload = message.payload or {}
    survivor = payload.get("mergedIntoVehicleId")
    if not survivor:
        raise ValueError(f"vehicle.vehicle_mdm.merged message {message.id} names no mergedIntoVehicleId.")
    move_stock_items_on_vehicle_merge(
        db, duplicate_vehicle_id=message.aggregate_id, survivor_vehicle_id=uuid.UUID(survivor)
    )
