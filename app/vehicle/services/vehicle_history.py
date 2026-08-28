"""Odometer + accessory + custody service layer (WP-5 PR-5)."""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.vehicle.models.vehicle_history import (
    CustodyEventType,
    OdometerSource,
    VehicleAccessory,
    VehicleCustodyEvent,
    VehicleOdometerReading,
)


def record_odometer_reading(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    value: int,
    reading_date: dt.date,
    source: OdometerSource,
    recording_tenant_id: uuid.UUID,
) -> VehicleOdometerReading:
    """A lower reading is accepted and flagged, never rejected (amended
    FR-V-07) — implausible is decided once, here, against whatever the
    current mileage was AT THIS MOMENT, and stored permanently on the row.
    It is not recomputed later, so an out-of-order backfill import can't
    silently flip the flag on a reading nobody re-reads.
    """

    current = current_mileage(db, vehicle_id=vehicle_id)
    implausible = current is not None and value < current.value

    reading = VehicleOdometerReading(
        vehicle_id=vehicle_id,
        value=value,
        reading_date=reading_date,
        source=source,
        implausible=implausible,
        recording_tenant_id=recording_tenant_id,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def current_mileage(db: Session, *, vehicle_id: uuid.UUID) -> VehicleOdometerReading | None:
    """Most recent reading BY READING DATE, not insert date — an implausible
    (lower) reading never displaces a genuinely later, higher one just
    because it was entered afterwards; ties broken by insert order via id.
    """

    return db.scalar(
        select(VehicleOdometerReading)
        .where(VehicleOdometerReading.vehicle_id == vehicle_id)
        .order_by(VehicleOdometerReading.reading_date.desc(), VehicleOdometerReading.id.desc())
        .limit(1)
    )


def list_odometer_readings(db: Session, *, vehicle_id: uuid.UUID) -> list[VehicleOdometerReading]:
    """Every reading, implausible ones included and clearly flagged — never
    excluded, never behind a toggle (amended FR-V-07's explicit rule).
    """

    return list(
        db.scalars(
            select(VehicleOdometerReading)
            .where(VehicleOdometerReading.vehicle_id == vehicle_id)
            .order_by(VehicleOdometerReading.reading_date.desc())
        ).all()
    )


def add_accessory(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    accessory_type: str,
    description: str | None,
    valid_from: dt.date,
    recording_tenant_id: uuid.UUID,
) -> VehicleAccessory:
    accessory = VehicleAccessory(
        vehicle_id=vehicle_id,
        accessory_type=accessory_type,
        description=description,
        valid_from=valid_from,
        valid_to=None,
        recording_tenant_id=recording_tenant_id,
    )
    db.add(accessory)
    db.commit()
    db.refresh(accessory)
    return accessory


def remove_accessory(db: Session, *, accessory: VehicleAccessory, valid_to: dt.date) -> VehicleAccessory:
    """Sets valid_to; the row is never deleted (FR-V-13)."""

    accessory.valid_to = valid_to
    db.commit()
    db.refresh(accessory)
    return accessory


def record_custody_event(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    partner_id: uuid.UUID,
    event_type: CustodyEventType,
    event_date: dt.datetime,
    transaction_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> VehicleCustodyEvent:
    event = VehicleCustodyEvent(
        vehicle_id=vehicle_id,
        partner_id=partner_id,
        event_type=event_type,
        event_date=event_date,
        transaction_id=transaction_id,
        created_by=actor_id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
