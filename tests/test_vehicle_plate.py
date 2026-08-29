"""WP-5 PR-4: plates — the two rules that need their own tests, not a
code review (ADR-039).
"""

import datetime as dt
import uuid

from app.vehicle.services.plate import record_plate_assignment, resolve_plate
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TODAY = dt.date(2026, 8, 28)
TENANT_ID = uuid.uuid4()


def _vehicle(db_session, vin: str):
    return create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)


def test_wechselschild_pair_never_raises_a_conflict(db_session):
    car_a = _vehicle(db_session, "ZAR94000007123456")
    car_b = _vehicle(db_session, "WVWZZZ1JZXW000001")
    group_id = uuid.uuid4()

    record_plate_assignment(
        db_session, vehicle_id=car_a.id, plate="TG 41277", canton="TG", valid_from=TODAY, valid_to=None,
        is_interchangeable=True, plate_group_id=group_id, recording_tenant_id=TENANT_ID,
    )
    record_plate_assignment(
        db_session, vehicle_id=car_b.id, plate="TG 41277", canton="TG", valid_from=TODAY, valid_to=None,
        is_interchangeable=True, plate_group_id=group_id, recording_tenant_id=TENANT_ID,
    )

    from app.vehicle.models.plate import VehiclePlateConflict

    conflicts = db_session.query(VehiclePlateConflict).filter_by(plate="TG 41277").all()
    assert conflicts == []


def test_two_unrelated_current_assignments_raise_exactly_one_conflict(db_session):
    car_a = _vehicle(db_session, "ZAR94000007123456")
    car_b = _vehicle(db_session, "WVWZZZ1JZXW000001")

    record_plate_assignment(
        db_session, vehicle_id=car_a.id, plate="VD 178204", canton="VD", valid_from=TODAY, valid_to=None,
        is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    record_plate_assignment(
        db_session, vehicle_id=car_b.id, plate="VD 178204", canton="VD", valid_from=TODAY, valid_to=None,
        is_interchangeable=False, plate_group_id=None, recording_tenant_id=uuid.uuid4(),
    )

    from app.vehicle.models.plate import VehiclePlateConflict

    conflicts = db_session.query(VehiclePlateConflict).filter_by(plate="VD 178204").all()
    assert len(conflicts) == 1


def test_assignments_at_different_dates_are_not_a_conflict(db_session):
    """The plate was reassigned; both records are true at their own dates
    — not a conflict at all, per the PRD's own text.
    """

    car_a = _vehicle(db_session, "ZAR94000007123456")
    car_b = _vehicle(db_session, "WVWZZZ1JZXW000001")

    record_plate_assignment(
        db_session, vehicle_id=car_a.id, plate="ZH 284611", canton="ZH",
        valid_from=TODAY - dt.timedelta(days=365), valid_to=TODAY - dt.timedelta(days=30),
        is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )
    record_plate_assignment(
        db_session, vehicle_id=car_b.id, plate="ZH 284611", canton="ZH",
        valid_from=TODAY - dt.timedelta(days=29), valid_to=None,
        is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )

    from app.vehicle.models.plate import VehiclePlateConflict

    conflicts = db_session.query(VehiclePlateConflict).filter_by(plate="ZH 284611").all()
    assert conflicts == []


def test_resolve_plate_defaults_to_valid_today_and_historical_is_explicit(db_session):
    car = _vehicle(db_session, "ZAR94000007123456")
    record_plate_assignment(
        db_session, vehicle_id=car.id, plate="AG 500000", canton="AG",
        valid_from=TODAY - dt.timedelta(days=365), valid_to=TODAY - dt.timedelta(days=30),
        is_interchangeable=False, plate_group_id=None, recording_tenant_id=TENANT_ID,
    )

    assert resolve_plate(db_session, plate="AG 500000", canton="AG", as_of=TODAY) == []
    historical = resolve_plate(
        db_session, plate="AG 500000", canton="AG", as_of=TODAY, include_historical=True
    )
    assert len(historical) == 1
