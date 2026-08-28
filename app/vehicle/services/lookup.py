"""FR-V-14 shared identity lookup (WP-5 PR-6, ADR-043). Any authenticated
tenant may resolve a vehicle already identified in vehicle-mdm by VIN,
Stammnummer, plate or vehicle number — without a provider call, and
without any tenant/group scoping at all. This is a genuinely global read,
a third scoping pattern beyond app.core.tenancy's tenant-scoped
get_or_404 and group-scoped get_group_read_or_404.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.lookup import SharedVehicleIdentity
from app.vehicle.services.plate import resolve_plate


def resolve_shared_identity(
    db: Session,
    *,
    vin: str | None = None,
    stammnummer: str | None = None,
    vehicle_number: str | None = None,
    plate: str | None = None,
    canton: str | None = None,
) -> SharedVehicleIdentity | None:
    vehicle: VehicleMdm | None = None

    if vin:
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.vin == vin))
    elif stammnummer:
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.stammnummer == stammnummer))
    elif vehicle_number:
        vehicle = db.scalar(select(VehicleMdm).where(VehicleMdm.vehicle_number == vehicle_number))
    elif plate and canton:
        rows = resolve_plate(db, plate=plate, canton=canton)
        candidate_ids = {row.vehicle_id for row in rows}
        if len(candidate_ids) == 1:
            vehicle = db.get(VehicleMdm, next(iter(candidate_ids)))

    if vehicle is None:
        return None

    return _to_shared_identity(db, vehicle)


def _current_plate(db: Session, vehicle: VehicleMdm) -> str | None:
    today = dt.datetime.now(dt.UTC).date()
    row = db.scalar(
        select(VehiclePlate)
        .where(
            VehiclePlate.vehicle_id == vehicle.id,
            VehiclePlate.valid_from <= today,
        )
        .order_by(VehiclePlate.valid_from.desc())
    )
    if row is not None and (row.valid_to is None or row.valid_to >= today):
        return row.plate
    return None


def _to_shared_identity(db: Session, vehicle: VehicleMdm) -> SharedVehicleIdentity:
    variant = vehicle.catalogue_variant
    return SharedVehicleIdentity(
        vin=vehicle.vin,
        stammnummer=vehicle.stammnummer,
        type_approval_number=vehicle.type_approval_number,
        first_registration_date=vehicle.first_registration_date,
        current_plate=_current_plate(db, vehicle),
        fuel_type=variant.fuel_type if variant else None,
        body_style=variant.body_style if variant else None,
        drivetrain=variant.drivetrain if variant else None,
        vehicle_status=vehicle.vehicle_status.value,
    )
