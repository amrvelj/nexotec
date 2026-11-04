"""Odometer + accessory + custody service layer (WP-5 PR-5)."""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
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


ACCESSORY_ENTITY_TYPE = "vehicle_accessory"
ACTION_ACCESSORY_ENDED = "accessory_ended"


def _accessory_audit(accessory: VehicleAccessory) -> dict[str, object]:
    return {
        "vehicleId": str(accessory.vehicle_id),
        "accessoryType": accessory.accessory_type,
        "description": accessory.description,
        "validFrom": accessory.valid_from.isoformat(),
        "validTo": accessory.valid_to.isoformat() if accessory.valid_to else None,
        "recordingTenantId": str(accessory.recording_tenant_id),
    }


def remove_accessory(
    db: Session,
    *,
    accessory: VehicleAccessory,
    valid_to: dt.date,
    actor_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
) -> VehicleAccessory:
    """Sets valid_to; the row is never deleted (FR-V-13).

    KAN-427 (Anto, 2026-11-03, option B): the car is shared, so any Betrieb
    with vehicle_mdm write may end an accessory another Betrieb recorded —
    and every end is audited with the actor, the ending Betrieb
    (`tenant_id`) and the row as it was (its recording Betrieb included), so
    an end by someone else is never untraceable. An accessory already ended
    is left as it is: a repeated DELETE neither moves its end date nor
    writes a second audit row.
    """

    if accessory.valid_to is not None:
        return accessory
    before = _accessory_audit(accessory)
    accessory.valid_to = valid_to
    record_audit_event(
        db,
        entity_type=ACCESSORY_ENTITY_TYPE,
        entity_id=accessory.id,
        tenant_id=tenant_id,
        action=ACTION_ACCESSORY_ENDED,
        actor_id=actor_id,
        before=before,
        after=_accessory_audit(accessory),
    )
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
