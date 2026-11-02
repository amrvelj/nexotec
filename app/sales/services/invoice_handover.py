"""KAN-523 — the plate a sold car leaves with, entered at «Rechnung anfordern»
(Anto, 2026-11-01: "within the «Rechnung anfordern» process. This will be
merged once into «Rechnung generieren»" — so it belongs to the invoicing
step, not to a screen of its own).

Sales stores no plate. It asks vehicle-mdm whether the plate can be recorded
on the car (a read, on a session of its own: naming the group's other car is
an audited plate read that commits its audit rows), and hands the answer on
in `sales.contract.invoice_requested`, whose vehicle consumer records it
(`app/vehicle/consumers.py`). A plate current on another car of the group is
refused until the user confirms the move (Anto, 2026-11-01: ask, as Vehicle
360 does), and the confirmation travels as `moveFrom`.
"""

import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.inventory.public import get_stock_item_vehicle
from app.sales.models.contract import SalesContract
from app.vehicle.public import PlateVersionRef, check_plate_handover


@dataclass(frozen=True)
class HandoverPlate:
    """The plate as the user typed it, and the moves they confirmed."""

    plate: str
    canton: str
    move_from: Sequence[tuple[uuid.UUID, int]] = ()


def swiss_today() -> dt.date:
    """The day invoicing is requested, in Switzerland — never the UTC day."""

    return dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()


def handover_payload(
    db: Session,
    *,
    contract: SalesContract,
    group_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    plate: HandoverPlate | None,
    requested_on: dt.date,
    session_factory: Callable[[], Session],
) -> dict:
    """The event's handover fields (the contract in `app/vehicle/consumers.py`);
    the car's `vehicleId` and `vin` with a plate only — without one there is
    nothing for vehicle-mdm to record. Raises the register's refusals for a plate that cannot be recorded, before
    the caller writes anything."""

    fields: dict = {
        "vehicleId": None,
        "vin": None,
        "groupId": str(group_id),
        "plate": None,
        "canton": None,
        "moveFrom": [],
        "requestedOn": requested_on.isoformat(),
        "requestedBy": str(actor_id) if actor_id else None,
    }
    if plate is None:
        return fields
    vehicle: dict = {"vehicleId": None, "vin": None}
    if contract.stock_item_id is not None:
        vehicle = get_stock_item_vehicle(db, tenant_id=contract.tenant_id, stock_item_id=contract.stock_item_id)
    if vehicle["vehicleId"] is None or actor_id is None:
        raise ConflictError(
            f"The vehicle of contract {contract.contract_number} has no vehicle record to carry a plate.",
            details={"reason": "invoice_plate_vehicle_unknown"},
        )

    short_lived = session_factory()
    try:
        check = check_plate_handover(
            short_lived,
            actor_id=actor_id,
            tenant_id=contract.tenant_id,
            group_id=group_id,
            vehicle_id=vehicle["vehicleId"],
            plate=plate.plate,
            canton=plate.canton,
            valid_from=contract.delivery_date or requested_on,
            move_from=[PlateVersionRef(plate_id, version) for plate_id, version in plate.move_from],
        )
    finally:
        short_lived.close()

    fields["vehicleId"] = str(vehicle["vehicleId"])
    fields["vin"] = vehicle["vin"]
    fields["plate"] = check.plate
    fields["canton"] = check.canton
    fields["moveFrom"] = (
        [] if check.already_on_vehicle else [{"plateId": str(p), "version": v} for p, v in plate.move_from]
    )
    return fields
