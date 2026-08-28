"""WP-5 PR-6: the matching waterfall stops at the first decisive/probable
rung rather than falling through.
"""

import datetime as dt
import uuid

from app.vehicle.services.matching import match_vehicle
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()


def test_vin_exact_is_decisive_and_wins_over_everything_else(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None, stammnummer="123456789")
    result = match_vehicle(db_session, vin="ZAR94000007123456", stammnummer="000000000")
    assert result.rung == "vin"
    assert result.decisive is True
    assert result.requires_confirmation is False
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_stammnummer_exact_is_decisive(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None, stammnummer="123456789")
    result = match_vehicle(db_session, stammnummer="123456789")
    assert result.rung == "stammnummer"
    assert result.decisive is True
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_plate_match_is_probable_and_requires_confirmation(db_session):
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    record_plate_assignment(
        db_session, vehicle_id=vehicle.id, plate="ZH 284611", canton="ZH", valid_from=dt.date(2026, 1, 1),
        valid_to=None, is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    result = match_vehicle(db_session, plate="ZH 284611", canton="ZH")
    assert result.rung == "plate"
    assert result.decisive is False
    assert result.requires_confirmation is True
    assert result.vehicle is not None and result.vehicle.id == vehicle.id


def test_no_identifiers_match_falls_through_to_no_match(db_session):
    result = match_vehicle(db_session, vin="NOSUCHVIN00000000")
    assert result.rung == "no_match"
    assert result.vehicle is None
    assert result.decisive is False


def test_type_approval_match_code_2_always_requires_confirmation(db_session):
    create_vehicle_mdm(
        db_session, vin="ZAR94000007123456", catalogue_variant_id=None, type_approval_number="1AB234"
    )
    create_vehicle_mdm(
        db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None, type_approval_number="1AB234"
    )
    result = match_vehicle(db_session, type_approval_number="1AB234")
    assert result.rung == "type_approval"
    assert result.match_code == 2
    assert result.requires_confirmation is True
