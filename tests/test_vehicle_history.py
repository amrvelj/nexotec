"""WP-5 PR-5: odometer, accessories, custody."""

import datetime as dt
import uuid

from app.vehicle.models.vehicle_history import CustodyEventType, OdometerSource
from app.vehicle.services.vehicle_history import (
    add_accessory,
    current_mileage,
    list_odometer_readings,
    record_custody_event,
    record_odometer_reading,
    remove_accessory,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()


def _vehicle(db_session):
    return create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)


def test_decreasing_reading_is_accepted_flagged_and_never_hidden(db_session):
    vehicle = _vehicle(db_session)
    record_odometer_reading(
        db_session, vehicle_id=vehicle.id, value=50000, reading_date=dt.date(2026, 6, 1),
        source=OdometerSource.SERVICE_VISIT, recording_tenant_id=TENANT_ID,
    )
    lower = record_odometer_reading(
        db_session, vehicle_id=vehicle.id, value=40000, reading_date=dt.date(2026, 7, 1),
        source=OdometerSource.MANUAL, recording_tenant_id=TENANT_ID,
    )

    assert lower.implausible is True
    # never hidden — it shows up in the ordinary reading list with no
    # special flag needed to see it
    readings = list_odometer_readings(db_session, vehicle_id=vehicle.id)
    assert lower.id in {r.id for r in readings}
    assert len(readings) == 2


def test_current_mileage_is_most_recent_by_reading_date_not_insert_order(db_session):
    vehicle = _vehicle(db_session)
    # Inserted out of chronological order on purpose.
    record_odometer_reading(
        db_session, vehicle_id=vehicle.id, value=30000, reading_date=dt.date(2026, 5, 1),
        source=OdometerSource.IMPORT, recording_tenant_id=TENANT_ID,
    )
    record_odometer_reading(
        db_session, vehicle_id=vehicle.id, value=45000, reading_date=dt.date(2026, 8, 1),
        source=OdometerSource.SALE, recording_tenant_id=TENANT_ID,
    )
    record_odometer_reading(
        db_session, vehicle_id=vehicle.id, value=38000, reading_date=dt.date(2026, 6, 15),
        source=OdometerSource.SERVICE_VISIT, recording_tenant_id=TENANT_ID,
    )

    current = current_mileage(db_session, vehicle_id=vehicle.id)
    assert current is not None and current.value == 45000


def test_removing_an_accessory_sets_valid_to_and_never_deletes(db_session):
    vehicle = _vehicle(db_session)
    accessory = add_accessory(
        db_session, vehicle_id=vehicle.id, accessory_type="towbar", description="Fitted at dealer",
        valid_from=dt.date(2024, 1, 1), recording_tenant_id=TENANT_ID,
    )
    accessory_id = accessory.id

    removed = remove_accessory(db_session, accessory=accessory, valid_to=dt.date(2026, 1, 1))
    assert removed.valid_to == dt.date(2026, 1, 1)

    from sqlalchemy import select

    from app.vehicle.models.vehicle_history import VehicleAccessory

    still_there = db_session.scalar(select(VehicleAccessory).where(VehicleAccessory.id == accessory_id))
    assert still_there is not None
    assert still_there.valid_to == dt.date(2026, 1, 1)


def test_custody_event_is_recorded(db_session):
    vehicle = _vehicle(db_session)
    event = record_custody_event(
        db_session,
        vehicle_id=vehicle.id,
        partner_id=uuid.uuid4(),
        event_type=CustodyEventType.ACQUIRED,
        event_date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    assert event.id is not None
    assert event.event_type == CustodyEventType.ACQUIRED
