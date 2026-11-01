"""The Kontrollschild register (ADR-039; KAN-467 — Anto, 2026-10-31: one
register per dealer group, and a plate current on another car of the group
moves once the user confirms). The rules that need their own tests, not a
code review: one spelling, the group's register only, the move, the
Wechselschild of at most two, ending without deleting, no plate value in an
audit row, and two concurrent recordings never both winning.
"""

import datetime as dt
import os
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError, UnprocessableEntityError
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.services.plate import (
    PLATE_ENTITY_TYPE,
    end_plate,
    normalise_kontrollschild,
    record_plate,
    resolve_plate,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TODAY = dt.date(2026, 8, 28)
GROUP = uuid.uuid4()
OTHER_GROUP = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def _vehicle(db_session, vin: str):
    return create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)


def _record(db_session, vehicle, plate="ZH 123456", *, group=GROUP, valid_from=TODAY, **kwargs) -> VehiclePlate:
    row = record_plate(
        db_session, vehicle_id=vehicle.id, group_id=group, recording_tenant_id=TENANT_ID, actor_id=None,
        plate=plate, canton=plate.split()[0], valid_from=valid_from, **kwargs,
    )
    db_session.commit()
    return row


def _reason(excinfo) -> str:
    return excinfo.value.details["reason"]


@pytest.mark.parametrize("typed", ["ZH 123456", "zh123456", "123456", "ZH 123 456", "ZH-123.456"])
def test_a_plate_is_stored_in_one_spelling(typed):
    assert normalise_kontrollschild(canton="zh", plate=typed) == ("ZH", "ZH 123456")


@pytest.mark.parametrize(
    ("canton", "plate", "reason"),
    [("XX", "123456", "plate_canton_invalid"), ("ZH", "0123", "plate_invalid"), ("ZH", "1234567", "plate_invalid"),
     ("ZH", "BE 1234", "plate_invalid"), ("ZH", "", "plate_invalid")],
)
def test_what_is_no_kontrollschild_is_refused(canton, plate, reason):
    with pytest.raises(UnprocessableEntityError) as excinfo:
        normalise_kontrollschild(canton=canton, plate=plate)
    assert _reason(excinfo) == reason


def test_a_plate_current_on_another_car_of_the_group_is_refused_naming_that_car(db_session):
    old_car = _vehicle(db_session, "ZAR94000007123456")
    new_car = _vehicle(db_session, "WVWZZZ1JZXW000001")
    held = _record(db_session, old_car, valid_from=dt.date(2024, 3, 1))

    with pytest.raises(ConflictError) as excinfo:
        _record(db_session, new_car)
    db_session.rollback()

    assert _reason(excinfo) == "plate_current_on_other_vehicle"
    [holder] = excinfo.value.details["holders"]
    assert holder["plateId"] == str(held.id)
    assert (holder["vehicleNumber"], holder["vin"]) == (old_car.vehicle_number, old_car.vin)
    assert holder["validFrom"] == "2024-03-01"
    assert db_session.scalars(select(VehiclePlate).where(VehiclePlate.vehicle_id == new_car.id)).all() == []


def test_confirming_the_move_ends_the_other_assignment_the_day_before(db_session):
    old_car = _vehicle(db_session, "ZAR94000007123456")
    new_car = _vehicle(db_session, "WVWZZZ1JZXW000001")
    held = _record(db_session, old_car, valid_from=dt.date(2024, 3, 1))

    moved = _record(db_session, new_car, move_from_plate_ids=[held.id])

    db_session.refresh(held)
    assert held.valid_to == TODAY - dt.timedelta(days=1)
    assert held.version == 2
    assert (moved.vehicle_id, moved.valid_from, moved.valid_to) == (new_car.id, TODAY, None)
    [current] = resolve_plate(db_session, group_id=GROUP, plate="ZH 123456", canton="ZH", as_of=TODAY)
    assert current.vehicle_id == new_car.id


def test_a_move_names_exactly_the_assignments_the_refusal_listed(db_session):
    old_car = _vehicle(db_session, "ZAR94000007123456")
    new_car = _vehicle(db_session, "WVWZZZ1JZXW000001")
    _record(db_session, old_car, valid_from=dt.date(2024, 3, 1))

    with pytest.raises(ConflictError) as excinfo:
        _record(db_session, new_car, move_from_plate_ids=[uuid.uuid4()])
    db_session.rollback()

    assert _reason(excinfo) == "plate_current_on_other_vehicle"


def test_a_plate_cannot_move_from_an_assignment_that_starts_later(db_session):
    old_car = _vehicle(db_session, "ZAR94000007123456")
    new_car = _vehicle(db_session, "WVWZZZ1JZXW000001")
    held = _record(db_session, old_car, valid_from=TODAY)

    with pytest.raises(ConflictError) as excinfo:
        _record(db_session, new_car, valid_from=TODAY, move_from_plate_ids=[held.id])
    db_session.rollback()

    assert _reason(excinfo) == "plate_move_before_start"


def test_a_wechselschild_joins_two_cars_and_never_a_third(db_session):
    car_a = _vehicle(db_session, "ZAR94000007123456")
    car_b = _vehicle(db_session, "WVWZZZ1JZXW000001")
    car_c = _vehicle(db_session, "1HGCM82633A004352")
    first = _record(db_session, car_a, "TG 41277", valid_from=dt.date(2024, 1, 1))

    second = _record(db_session, car_b, "TG 41277", wechselschild_with_plate_id=first.id)

    db_session.refresh(first)
    assert first.plate_group_id is not None and first.plate_group_id == second.plate_group_id
    assert first.is_interchangeable and second.is_interchangeable
    pair = resolve_plate(db_session, group_id=GROUP, plate="TG 41277", canton="TG", as_of=TODAY)
    assert {r.vehicle_id for r in pair} == {car_a.id, car_b.id}

    with pytest.raises(ConflictError) as excinfo:
        _record(db_session, car_c, "TG 41277", wechselschild_with_plate_id=first.id)
    db_session.rollback()
    assert _reason(excinfo) == "plate_wechselschild_not_possible"
    assert len(excinfo.value.details["holders"]) == 2


def test_the_same_plate_twice_on_one_car_is_refused(db_session):
    car = _vehicle(db_session, "ZAR94000007123456")
    _record(db_session, car)

    with pytest.raises(ConflictError) as excinfo:
        _record(db_session, car, valid_from=TODAY + dt.timedelta(days=3))
    db_session.rollback()

    assert _reason(excinfo) == "plate_already_on_vehicle"


def test_another_groups_register_is_never_read_or_moved(db_session):
    """Anto, 2026-10-31: two groups may each hold ZH 123456 on different cars;
    neither sees, matches or moves the other's."""

    ours = _vehicle(db_session, "ZAR94000007123456")
    theirs = _vehicle(db_session, "WVWZZZ1JZXW000001")
    our_row = _record(db_session, ours, valid_from=dt.date(2024, 3, 1))

    their_row = _record(db_session, theirs, group=OTHER_GROUP)

    db_session.refresh(our_row)
    assert our_row.valid_to is None  # not moved
    assert [r.id for r in resolve_plate(db_session, group_id=GROUP, plate="ZH 123456", canton="ZH", as_of=TODAY)] == [
        our_row.id
    ]
    assert [
        r.id for r in resolve_plate(db_session, group_id=OTHER_GROUP, plate="ZH 123456", canton="ZH", as_of=TODAY)
    ] == [their_row.id]


def test_ending_an_assignment_takes_its_version_and_keeps_the_row(db_session):
    car = _vehicle(db_session, "ZAR94000007123456")
    row = _record(db_session, car, valid_from=dt.date(2024, 3, 1))

    with pytest.raises(ConflictError):
        end_plate(db_session, row=row, valid_to=TODAY, expected_version=7, actor_id=None, tenant_id=TENANT_ID)
    db_session.rollback()
    with pytest.raises(UnprocessableEntityError) as before_start:
        end_plate(
            db_session, row=row, valid_to=dt.date(2024, 2, 1), expected_version=1, actor_id=None, tenant_id=TENANT_ID
        )
    db_session.rollback()
    assert _reason(before_start) == "plate_validity_invalid"

    end_plate(db_session, row=row, valid_to=TODAY, expected_version=1, actor_id=None, tenant_id=TENANT_ID)
    db_session.commit()

    db_session.refresh(row)
    assert (row.valid_to, row.version) == (TODAY, 2)
    with pytest.raises(ConflictError) as again:
        end_plate(db_session, row=row, valid_to=TODAY, expected_version=2, actor_id=None, tenant_id=TENANT_ID)
    db_session.rollback()
    assert _reason(again) == "plate_already_ended"
    assert db_session.get(VehiclePlate, row.id) is not None


def test_resolve_plate_defaults_to_valid_today_and_historical_is_explicit(db_session):
    car = _vehicle(db_session, "ZAR94000007123456")
    _record(
        db_session, car, "AG 500000",
        valid_from=TODAY - dt.timedelta(days=365), valid_to=TODAY - dt.timedelta(days=30),
    )

    assert resolve_plate(db_session, group_id=GROUP, plate="AG 500000", canton="AG", as_of=TODAY) == []
    historical = resolve_plate(
        db_session, group_id=GROUP, plate="AG 500000", canton="AG", as_of=TODAY, include_historical=True
    )
    assert len(historical) == 1
    # A value that is no Kontrollschild finds nothing rather than raising.
    assert resolve_plate(db_session, group_id=GROUP, plate="nonsense", canton="AG", include_historical=True) == []


def test_no_audit_row_of_a_plate_write_carries_the_plate_value(db_session):
    """The audit log is listable by a manager; plate values in it would make
    the register enumerable through the back door (ADR-039, ADR-085)."""

    old_car = _vehicle(db_session, "ZAR94000007123456")
    new_car = _vehicle(db_session, "WVWZZZ1JZXW000001")
    held = _record(db_session, old_car, "ZH 284611", valid_from=dt.date(2024, 3, 1))
    _record(db_session, new_car, "ZH 284611", move_from_plate_ids=[held.id])

    rows = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_type == PLATE_ENTITY_TYPE)).all()
    assert {r.action for r in rows} == {"plate_recorded", "plate_ended"}
    for row in rows:
        assert "284611" not in repr((row.before, row.after, row.reason))


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="advisory locks are Postgres-only")
def test_two_concurrent_recordings_of_one_plate_never_both_land(db_session, engine, monkeypatch):
    """The first recording pauses after its insert, before its commit; the
    second starts meanwhile. Without the per-plate lock the second sees the
    plate free and inserts too: two current rows of one plate on two cars of
    one group."""

    from app.vehicle.services import plate as plate_service

    car_a = _vehicle(db_session, "ZAR94000007123456")
    car_b = _vehicle(db_session, "WVWZZZ1JZXW000001")
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    first_inside, second_started = threading.Event(), threading.Event()
    real_audit = plate_service.record_audit_event

    def _pausing_audit(db, **kwargs):
        if threading.current_thread().name == "first" and not first_inside.is_set():
            first_inside.set()
            second_started.wait(timeout=10)
            threading.Event().wait(0.5)  # let the second reach its read
        return real_audit(db, **kwargs)

    monkeypatch.setattr(plate_service, "record_audit_event", _pausing_audit)
    outcomes: list[str] = []

    def _attempt(vehicle_id):
        with factory() as session:
            try:
                record_plate(
                    session, vehicle_id=vehicle_id, group_id=GROUP, recording_tenant_id=TENANT_ID, actor_id=None,
                    plate="ZH 777", canton="ZH", valid_from=TODAY,
                )
                session.commit()
                outcomes.append("recorded")
            except ConflictError as exc:
                session.rollback()
                outcomes.append(exc.details["reason"])

    first = threading.Thread(target=_attempt, args=(car_a.id,), name="first")
    second = threading.Thread(target=_attempt, args=(car_b.id,), name="second")
    first.start()
    assert first_inside.wait(timeout=10)
    second.start()
    second_started.set()
    first.join(timeout=20)
    second.join(timeout=20)

    assert sorted(outcomes) == ["plate_current_on_other_vehicle", "recorded"]
    current = resolve_plate(db_session, group_id=GROUP, plate="ZH 777", canton="ZH", as_of=TODAY)
    assert len(current) == 1
