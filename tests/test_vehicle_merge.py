"""WP-5 PR-6: vehicle merge — one-way, re-points everything, no unmerge."""

import datetime as dt
import uuid

import pytest

from app.vehicle.services.merge import merge_vehicles, resolve_merged_id
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.vehicle_history import add_accessory, record_odometer_reading
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()


def test_merge_repoints_plates_accessories_and_odometer_readings(db_session):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)

    plate = record_plate_assignment(
        db_session, vehicle_id=duplicate.id, plate="ZH 284611", canton="ZH", valid_from=dt.date(2026, 1, 1),
        valid_to=None, is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    accessory = add_accessory(
        db_session, vehicle_id=duplicate.id, accessory_type="towbar", description=None,
        valid_from=dt.date(2024, 1, 1), recording_tenant_id=TENANT_ID,
    )
    from app.vehicle.models.vehicle_history import OdometerSource

    reading = record_odometer_reading(
        db_session, vehicle_id=duplicate.id, value=10000, reading_date=dt.date(2026, 1, 1),
        source=OdometerSource.MANUAL, recording_tenant_id=TENANT_ID,
    )

    merged_survivor = merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    assert merged_survivor.id == survivor.id

    db_session.refresh(plate)
    db_session.refresh(accessory)
    db_session.refresh(reading)
    assert plate.vehicle_id == survivor.id
    assert accessory.vehicle_id == survivor.id
    assert reading.vehicle_id == survivor.id

    db_session.refresh(duplicate)
    assert duplicate.merged_into_vehicle_id == survivor.id


def test_merging_an_already_merged_vehicle_raises(db_session):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())

    another = create_vehicle_mdm(db_session, vin="1HGCM82633A004352", catalogue_variant_id=None)
    with pytest.raises(ValueError):
        merge_vehicles(db_session, survivor_id=another.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())


def test_resolve_merged_id_follows_the_chain(db_session):
    a = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    b = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    c = create_vehicle_mdm(db_session, vin="1HGCM82633A004352", catalogue_variant_id=None)

    merge_vehicles(db_session, survivor_id=b.id, duplicate_id=a.id, actor_id=uuid.uuid4())
    merge_vehicles(db_session, survivor_id=c.id, duplicate_id=b.id, actor_id=uuid.uuid4())

    assert resolve_merged_id(db_session, a.id) == c.id
    assert resolve_merged_id(db_session, c.id) == c.id
