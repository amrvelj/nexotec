"""KAN-634 (FR-V-12, ADR-131) — a vehicle merge reaches customer's party rows
as a fact, never as a call.

Vehicle merges two vehicle-mdm records and publishes
`vehicle.vehicle_mdm.merged` in the merge's own transaction
(`app/vehicle/services/merge.py`); customer, which sits above vehicle and
owns `VehicleParty`, consumes it here (`customer.vehicle_parties_on_vehicle_merged`,
registered in `app.worker.register_handlers`) and re-points every party row
naming the duplicate at the survivor, in its own transaction with the
processed_event row (rules 4, 6 and 12; ADR-047).

The event's contract: the duplicate is the aggregate (`aggregate_id`),
`tenant_id` is null (vehicle-mdm is global, ADR-022), and the payload carries
`vehicleId` (the duplicate) and `mergedIntoVehicleId` (the survivor the merge
named).

What the re-point does is what the synchronous call it replaces did, no more:
every row moves — every dealer group's, open, closed or planned — none is
closed, deleted, audited or published as `linked`/`unlinked`, and each takes
the survivor's label (KAN-84). The close rule is not applied to the moved rows
(KAN-634 leaves it untouched).
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.customer.models.vehicle_party import VehicleParty
from app.customer.services.vehicle_party_labels import relabel_vehicle_parties_for_vehicle
from app.vehicle.public import resolve_merged_vehicle_id

logger = logging.getLogger(__name__)


def repoint_vehicle_parties_on_vehicle_merge(
    db: Session, *, duplicate_vehicle_id: uuid.UUID, survivor_vehicle_id: uuid.UUID
) -> int:
    """Moves every party row naming the duplicate onto the survivor and
    relabels the survivor's rows from `app.vehicle.public` (never from the
    event). Flushes only — the consumer harness commits it with the
    processed_event row. Returns the number of rows moved.

    The survivor is resolved to the live record first: delivery is
    unordered, so A→B consumed after B→C must land A's rows on C, not on the
    merged B. A duplicate no row names moves nothing, so a late or repeated
    event is harmless even past the processed-events guard.

    The rows of both vehicles are locked FOR NO KEY UPDATE in one statement,
    in id order — the order every other writer of party rows takes them in
    (an allocation and a customer merge after their advisory locks, the
    summary-changed consumer) — so this never holds one vehicle's rows while
    waiting for the other's."""

    target = resolve_merged_vehicle_id(db, survivor_vehicle_id)
    if target == duplicate_vehicle_id:
        # A cycle the vehicle side never writes; moving rows onto the record
        # they are on would only relabel them.
        logger.warning("Vehicle merge of %s resolves back to itself; no party row moved.", duplicate_vehicle_id)
        return 0
    rows = list(
        db.scalars(
            select(VehicleParty)
            .where(VehicleParty.vehicle_id.in_((duplicate_vehicle_id, target)))
            .order_by(VehicleParty.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    moved = [party for party in rows if party.vehicle_id == duplicate_vehicle_id]
    if not moved:
        return 0
    for party in moved:
        party.vehicle_id = target
    db.flush()
    relabel_vehicle_parties_for_vehicle(db, vehicle_id=target)
    return len(moved)


def handle_vehicle_merged_message(db: Session, message: OutboxMessage) -> None:
    """The consumer of `vehicle.vehicle_mdm.merged`
    (`customer.vehicle_parties_on_vehicle_merged`). Flushes only;
    consume_once commits it together with the processed_event row."""

    payload = message.payload or {}
    survivor = payload.get("mergedIntoVehicleId")
    if not survivor:
        raise ValueError(f"vehicle.vehicle_mdm.merged message {message.id} names no mergedIntoVehicleId.")
    repoint_vehicle_parties_on_vehicle_merge(
        db, duplicate_vehicle_id=message.aggregate_id, survivor_vehicle_id=uuid.UUID(survivor)
    )
