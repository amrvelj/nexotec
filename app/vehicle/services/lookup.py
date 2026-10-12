"""FR-V-14 shared identity lookup (WP-5 PR-6, ADR-043). Any authenticated
tenant may resolve a vehicle already identified in vehicle-mdm by VIN,
Stammnummer, plate or vehicle number — without a provider call, and
without any tenant/group scoping at all. This is a genuinely global read,
a third scoping pattern beyond app.core.tenancy's tenant-scoped
get_or_404 and group-scoped get_group_read_or_404.

KAN-279: the answer carries the vehicle's current plate, so — like the
Plates tab and a resolved search hit (KAN-231) — every answer is one plate
read of one vehicle, through `plate_read_guard`: audited, and counted
against the caller's per-user limit. Past the limit the identity still
resolves and `current_plate` is None — ADR-043's locked shape has no field
to say why, so a withheld plate reads exactly like a vehicle without one.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.lookup import SharedVehicleIdentity
from app.vehicle.services.plate import resolve_plate
from app.vehicle.services.plate_read_guard import read_plate_history

PLATE_READ_PURPOSE = "shared_identity_lookup"


def resolve_shared_identity(
    db: Session,
    *,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    vin: str | None = None,
    stammnummer: str | None = None,
    vehicle_number: str | None = None,
    plate: str | None = None,
    canton: str | None = None,
) -> SharedVehicleIdentity | None:
    """`actor_id`/`tenant_id` are the caller's, from the token: the plate
    read is attributed and limited per user (KAN-279)."""

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

    return _to_shared_identity(db, vehicle, actor_id=actor_id, tenant_id=tenant_id)


def _plate_valid_on(plates: list[VehiclePlate], day: dt.date) -> str | None:
    """The plate valid on `day` from one vehicle's own history (newest
    first, as the guard returns it) — the search hit's rule (KAN-82)."""

    return next(
        (row.plate for row in plates if row.valid_from <= day and (row.valid_to is None or row.valid_to >= day)),
        None,
    )


def _to_shared_identity(
    db: Session, vehicle: VehicleMdm, *, actor_id: uuid.UUID, tenant_id: uuid.UUID
) -> SharedVehicleIdentity:
    # Every other field is read before the guard runs: it commits (its audit
    # row must survive whatever follows), which expires `vehicle`.
    variant = vehicle.catalogue_variant
    identity = SharedVehicleIdentity(
        vin=vehicle.vin,
        stammnummer=vehicle.stammnummer,
        type_approval_number=vehicle.type_approval_number,
        first_registration_date=vehicle.first_registration_date,
        current_plate=None,
        fuel_type=variant.fuel_type if variant else None,
        body_style=variant.body_style if variant else None,
        drivetrain=variant.drivetrain if variant else None,
        vehicle_status=vehicle.vehicle_status.value,
    )
    plates = read_plate_history(
        db, actor_id=actor_id, tenant_id=tenant_id, vehicle_id=vehicle.id, purpose=PLATE_READ_PURPOSE
    )
    if plates is not None:
        identity.current_plate = _plate_valid_on(plates, utcnow().date())
    return identity
