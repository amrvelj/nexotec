"""KAN-467 — seed the plate register the way the product writes it: through
`services/plate.py::record_plate`, in one dealer group's register, committed.
A Wechselschild partner is named by `wechselschild_with`, never by inventing a
plate_group_id the service did not form."""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.vehicle.models.plate import VehiclePlate
from app.vehicle.services.plate import PlateVersionRef, record_plate


def seed_plate(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    group_id: uuid.UUID,
    plate: str,
    canton: str | None = None,
    valid_from: dt.date = dt.date(2020, 1, 1),
    valid_to: dt.date | None = None,
    recording_tenant_id: uuid.UUID | None = None,
    wechselschild_with: VehiclePlate | None = None,
) -> VehiclePlate:
    row = record_plate(
        db,
        vehicle_id=vehicle_id,
        group_id=group_id,
        recording_tenant_id=recording_tenant_id or uuid.uuid4(),
        actor_id=None,
        plate=plate,
        canton=canton or plate.split()[0],
        valid_from=valid_from,
        valid_to=valid_to,
        wechselschild_with=(
            PlateVersionRef(wechselschild_with.id, wechselschild_with.version) if wechselschild_with is not None else None
        ),
    )
    db.commit()
    db.refresh(row)
    return row
