"""Vehicle merge (WP-5 PR-6, FR-V-12) — one-way, audit-logged, no unmerge.
Same shape as the customer merge (app.sales.services.transaction.
repoint_customer_transactions is that merge's own cross-context repoint
call), but this time WITH the re-pointing that one is still missing —
plates, accessories, odometer readings, custody events, and party links
all move to the survivor.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.audit import record_audit_event
from app.customer.public import repoint_vehicle_party
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleCustodyEvent, VehicleOdometerReading
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_or_404


def merge_vehicles(db: Session, *, survivor_id: uuid.UUID, duplicate_id: uuid.UUID, actor_id: uuid.UUID) -> VehicleMdm:
    """Re-points every same-context reference (plates, accessories,
    odometer readings, custody events) in ONE local transaction, marks the
    duplicate merged, and commits — THEN makes the cross-context call into
    customer.public to repoint VehicleParty as a SEPARATE call with its own
    commit (ADR-047: never a shared transaction, even though it would work
    today). If that second call fails, the vehicle-side merge already
    committed and is not rolled back — the nightly reconciliation job is
    what repairs a stuck VehicleParty reference, not a compensating
    rollback here.
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
    db.commit()
    db.refresh(survivor)

    repoint_vehicle_party(db, duplicate_vehicle_id=duplicate_id, survivor_vehicle_id=survivor_id)

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

    seen = set()
    current_id = vehicle_id
    while current_id not in seen:
        seen.add(current_id)
        vehicle = db.get(VehicleMdm, current_id)
        if vehicle is None or vehicle.merged_into_vehicle_id is None:
            return current_id
        current_id = vehicle.merged_into_vehicle_id
    return current_id  # cycle guard — should be unreachable in practice
