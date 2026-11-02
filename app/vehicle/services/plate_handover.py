"""KAN-523 — the plate a sold car leaves with (Anto, 2026-11-01: "Once a car
is invoiced it should automatically store the plate on the VIN on Vehicle
360").

Sales asks at «Rechnung anfordern» (`check_plate_handover`, through
`vehicle.public`), before it publishes `sales.contract.invoice_requested`;
the vehicle consumer of that event records the plate
(`record_plate_on_invoice_requested`, `app/vehicle/consumers.py`). The
register's rules stay `services/plate.py`'s: one spelling, one car of the
group at a time, a move only once the user confirmed it at the versions the
refusal showed (Anto, 2026-11-01: ask before moving, as Vehicle 360 does).
"""

import datetime as dt
import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.plate import (
    PlateRecordingCheck,
    PlateVersionRef,
    check_plate_recording,
    record_plate,
)
from app.vehicle.services.plate_read_guard import may_name_plate_holders

logger = logging.getLogger(__name__)

HOLDERS_PURPOSE = "invoice_plate_refused"


def check_plate_handover(
    db: Session,
    *,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    plate: str,
    canton: str,
    valid_from: dt.date,
    move_from: Sequence[PlateVersionRef] = (),
) -> PlateRecordingCheck:
    """Would recording this plate on the sold car be refused? Raises what
    `record_plate` would (422 spelling; 409 `plate_current_on_other_vehicle`
    until `move_from` answers for the cars named; a stale version), else
    returns the plate in its stored spelling.

    Naming the group's other car(s) is a plate read (ADR-085): the refusal
    passes them through the plate-read guard, audited and counted against
    the actor's limit exactly as Vehicle 360's «Kontrollschild erfassen»
    does; past the limit it carries `holdersWithheld` instead. The guard
    commits its audit rows, so the caller hands this a session of its own.
    """

    if db.get(VehicleMdm, vehicle_id) is None:
        raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
    try:
        return check_plate_recording(
            db,
            vehicle_id=vehicle_id,
            group_id=group_id,
            plate=plate,
            canton=canton,
            valid_from=valid_from,
            move_from=move_from,
        )
    except ConflictError as exc:
        details = exc.details or {}
        holders = details.get("holders")
        if holders:
            db.rollback()
            allowed = may_name_plate_holders(
                db,
                actor_id=actor_id,
                tenant_id=tenant_id,
                vehicle_ids=[uuid.UUID(holder["vehicleId"]) for holder in holders],
                purpose=HOLDERS_PURPOSE,
            )
            if not allowed:
                exc.details = {key: value for key, value in details.items() if key != "holders"} | {
                    "holdersWithheld": True
                }
        raise


def record_plate_on_invoice_requested(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    vin: str | None,
    plate: str,
    canton: str,
    valid_from: dt.date,
    move_from: Sequence[PlateVersionRef],
    actor_id: uuid.UUID | None,
) -> None:
    """Records the plate the contract's car was handed over with, in the
    delivering Betrieb's group register, from the day it is valid
    (the delivery date, or the day invoicing was requested).

    Flushes only: `consume_once` commits it with the processed-event row. A
    plate this car already carries from that day is no new recording (a
    second invoice request for the same car with the same plate). Any other
    refusal is raised: the event is retried and, still refused, dead-lettered
    — the dead-letter alarm is how a plate moved by someone else between the
    user's answer and this recording reaches a person (rule 10)."""

    vehicle = db.get(VehicleMdm, vehicle_id)
    if vehicle is None:
        raise ValueError(f"sales.contract.invoice_requested names vehicle {vehicle_id}, which does not exist.")
    if vin is not None and vehicle.vin != vin:
        raise ValueError(
            f"sales.contract.invoice_requested names vehicle {vehicle_id} with VIN {vin}, "
            f"but that vehicle's VIN is {vehicle.vin}."
        )
    try:
        record_plate(
            db,
            vehicle_id=vehicle_id,
            group_id=group_id,
            recording_tenant_id=tenant_id,
            actor_id=actor_id,
            plate=plate,
            canton=canton,
            valid_from=valid_from,
            move_from=move_from,
        )
    except ConflictError as exc:
        if (exc.details or {}).get("reason") == "plate_already_on_vehicle":
            logger.info("invoice_plate_already_recorded", extra={"vehicleId": str(vehicle_id)})
            return
        raise
