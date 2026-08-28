"""VehicleMdm service layer (WP-5 PR-3)."""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleNumberSequence


def allocate_vehicle_number(db: Session) -> str:
    """Allocate the next `F-000001`-style number. Global — unlike the
    per-group CustomerNumberSequence (app.customer.models.customer), a
    vehicle is a global fact (ADR-022), so there is exactly one counter,
    not one per group. Same row-lock-then-increment idiom, same "gaps are
    harmless, reuse is not" reasoning: a failed transaction burns a
    number on purpose rather than risk two vehicles sharing one.
    """

    row = db.get(VehicleNumberSequence, "GLOBAL", with_for_update=True)
    if row is None:
        row = VehicleNumberSequence(singleton_key="GLOBAL", next_value=1)
        db.add(row)
        db.flush()
        row = db.get(VehicleNumberSequence, "GLOBAL", with_for_update=True)
        assert row is not None, "just-flushed VehicleNumberSequence row vanished before it could be re-read"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"F-{value:06d}"


def get_vehicle_mdm_or_404(db: Session, vehicle_id: uuid.UUID) -> VehicleMdm:
    vehicle = db.get(VehicleMdm, vehicle_id)
    if vehicle is None:
        raise NotFoundError(f"Vehicle {vehicle_id} was not found.")
    return vehicle


def get_vehicle_mdm_by_vin(db: Session, vin: str) -> VehicleMdm | None:
    return db.scalar(select(VehicleMdm).where(VehicleMdm.vin == vin))


def create_vehicle_mdm(
    db: Session,
    *,
    vin: str,
    catalogue_variant_id: uuid.UUID | None,
    stammnummer: str | None = None,
    type_approval_number: str | None = None,
    first_registration_date: dt.date | None = None,
    actor_id: uuid.UUID | None = None,
) -> VehicleMdm:
    """FR-V-15's "a VIN that already exists is not a validation error"
    rule is enforced by the CALLER (the API layer resolves the existing
    record and offers to open it, per the brief) — this function's own
    IntegrityError handling is the last-resort guard against a race
    between that check and this insert, not the primary mechanism.
    """

    vehicle = VehicleMdm(
        vin=vin,
        vehicle_number=allocate_vehicle_number(db),
        stammnummer=stammnummer,
        type_approval_number=type_approval_number,
        first_registration_date=first_registration_date,
        catalogue_variant_id=catalogue_variant_id,
        catalogue_match_status=(
            CatalogueMatchStatus.MATCHED if catalogue_variant_id is not None else CatalogueMatchStatus.UNVERIFIED
        ),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"A vehicle with VIN '{vin}' already exists.", details={"vin": vin}) from exc

    db.commit()
    db.refresh(vehicle)
    return vehicle
