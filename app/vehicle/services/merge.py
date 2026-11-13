"""Vehicle merge (WP-5 PR-6, FR-V-12) — one-way, audit-logged, no unmerge.
Same shape as the customer merge, WITH the re-pointing: plates,
accessories, odometer readings and custody events move to the survivor here;
the party links (customer's `VehicleParty`) follow when customer consumes
`vehicle.vehicle_mdm.merged` (KAN-634, ADR-131: customer sits above vehicle,
so vehicle publishes the fact and never calls customer).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.audit import record_audit_event
from app.core.outbox import OutboxEvent, publish
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleCustodyEvent, VehicleOdometerReading
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404

MERGED_EVENT = "vehicle.vehicle_mdm.merged"


def merge_vehicles(db: Session, *, survivor_id: uuid.UUID, duplicate_id: uuid.UUID, actor_id: uuid.UUID) -> VehicleMdm:
    """Re-points every same-context reference (plates, accessories,
    odometer readings, custody events) in ONE local transaction, marks the
    duplicate merged, audits it and publishes `vehicle.vehicle_mdm.merged`
    in that same transaction (rule 4: business row and outbox row
    together), and commits.

    The party links are customer's rows: customer re-points them when it
    consumes the event (`customer.vehicle_parties_on_vehicle_merged`,
    KAN-634), in its own transaction — never in this one (ADR-047) and never
    by a call from here (ADR-131). Until the worker delivers it, the
    duplicate's holders still name the duplicate; nothing in this merge
    reads them. A delivery that fails is retried and then dead-lettered
    (rule 10), and nightly reconciliation reports a party row naming a
    merged vehicle.
    """

    survivor = get_vehicle_mdm_or_404(db, survivor_id)
    duplicate = get_vehicle_mdm_or_404(db, duplicate_id)
    if duplicate.merged_into_vehicle_id is not None:
        raise ValueError(f"Vehicle {duplicate_id} was already merged into {duplicate.merged_into_vehicle_id}.")

    repointed = {
        "vehicle_plate": _repoint(db, VehiclePlate, VehiclePlate.vehicle_id, duplicate_id, survivor_id),
        "vehicle_accessory": _repoint(db, VehicleAccessory, VehicleAccessory.vehicle_id, duplicate_id, survivor_id),
        "vehicle_odometer_reading": _repoint(
            db, VehicleOdometerReading, VehicleOdometerReading.vehicle_id, duplicate_id, survivor_id
        ),
        "vehicle_mdm_custody_event": _repoint(
            db, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id, duplicate_id, survivor_id
        ),
    }

    duplicate.merged_into_vehicle_id = survivor_id

    record_audit_event(
        db,
        entity_type="vehicle_mdm",
        entity_id=duplicate.id,
        tenant_id=None,
        action="merge",
        actor_id=actor_id,
        before={"mergedIntoVehicleId": None},
        after={"mergedIntoVehicleId": str(survivor_id), "repointed": repointed},
    )
    # vehicle-mdm is global (ADR-022): no Betrieb owns the fact, so
    # tenant_id is None and each consumer applies its own scope (customer
    # re-points every dealer group's party rows naming the duplicate).
    publish(
        db,
        OutboxEvent(
            event_type=MERGED_EVENT,
            tenant_id=None,
            producer="vehicle",
            aggregate_type="vehicle_mdm",
            aggregate_id=duplicate.id,
            payload={"vehicleId": str(duplicate.id), "mergedIntoVehicleId": str(survivor_id)},
        ),
    )
    db.commit()
    db.refresh(survivor)

    return survivor


def _repoint(
    db: Session,
    model: type[Any],
    vehicle_id_column: InstrumentedAttribute[uuid.UUID],
    duplicate_id: uuid.UUID,
    survivor_id: uuid.UUID,
) -> int:
    rows = list(db.scalars(select(model).where(vehicle_id_column == duplicate_id)).all())
    for row in rows:
        row.vehicle_id = survivor_id
    return len(rows)


def resolve_merged_id(db: Session, vehicle_id: uuid.UUID) -> uuid.UUID:
    """Follows merged_into_vehicle_id to the current survivor — a merge
    chain is possible (A merged into B, B later merged into C), so this
    walks until it finds an id that hasn't itself been merged away,
    guarding against a cycle that should never exist but must never hang
    if it somehow did.
    """

    # `seen |= {…}`, not `seen.add(…)`: the ADR-047 architecture test reads
    # any `.add` call as a session write, and this is a read
    # (`vehicle.public.resolve_merged_vehicle_id`, KAN-634).
    seen: set[uuid.UUID] = set()
    current_id = vehicle_id
    while current_id not in seen:
        seen |= {current_id}
        # The column, read from the database — never `db.get`, which answers
        # from the session's own copy of a row it loaded earlier: a party
        # write re-reads this under its lock to see a merge that committed
        # after the request first read the vehicle (KAN-673).
        merged_into = db.execute(
            select(VehicleMdm.merged_into_vehicle_id).where(VehicleMdm.id == current_id)
        ).one_or_none()
        if merged_into is None or merged_into[0] is None:
            return current_id
        current_id = merged_into[0]
    return current_id  # cycle guard — should be unreachable in practice
