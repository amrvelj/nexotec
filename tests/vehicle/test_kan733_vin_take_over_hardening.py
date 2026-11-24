"""KAN-733: hardening of KAN-730's VIN take-over. On Postgres.

1. A write that changes the car's own merged duplicate between the
   correction's unlocked read and its lock: the refusal names whoever
   carries the new VIN *now*, or is `vehicle_vin_changed` — never the stale
   row (which named the car itself and its mistyped VIN).
2. The take-over between two pre-1981 chassis-number records (KAN-535) and
   its compensation.
3. A re-merge of the pair while a correction holds the survivor is refused
   before it locks anything, so the two never wait on each other.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
from app.vehicle.public import correct_vehicle_vin
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.merge import merge_vehicle_records, merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.inventory.test_inventory_vin_correction import REASON
from tests.inventory.test_kan535_chassis_number_purchase import CHASSIS, PRE_1981
from tests.postgres_only import ROW_LOCKS, UUID_IN_RAW_SQL
from tests.vehicle.test_kan730_vin_taken_over_from_own_merged_duplicate import (
    RIGHT_VIN,
    SLIPPED_VIN,
    VIN,
    _car_and_duplicate,
    _correct_directly,
)


def _race(db_session, monkeypatch, write) -> None:
    """`write(session)` commits on its own session once, right after the
    correction's unlocked read of the duplicate and before its lock."""

    real = vehicle_mdm_service.live_vehicle_mdm
    fired: list[bool] = []

    def _racing(own, record):
        result = real(own, record)
        if not fired:
            fired.append(True)
            with Session(bind=db_session.get_bind()) as other:
                write(other)
                other.commit()
        return result

    monkeypatch.setattr(vehicle_mdm_service, "live_vehicle_mdm", _racing)


def _move_vin(session: Session, record_id: uuid.UUID, vin: str) -> None:
    session.execute(
        text("UPDATE vehicle_mdm SET vin = :vin, version = version + 1 WHERE id = :id"), {"vin": vin, "id": record_id}
    )


# --- 1. the refusal after a race ---------------------------------------------------


@UUID_IN_RAW_SQL
def test_nobody_carrying_the_vin_after_the_race_is_vehicle_vin_changed(db_session, monkeypatch):
    car_id, duplicate = _car_and_duplicate(db_session)
    _race(db_session, monkeypatch, lambda s: _move_vin(s, duplicate.id, SLIPPED_VIN))

    with pytest.raises(ConflictError) as refused:
        _correct_directly(db_session, car_id)

    assert refused.value.details == {"reason": "vehicle_vin_changed", "vehicleId": str(car_id)}
    db_session.expire_all()
    assert db_session.get(VehicleMdm, car_id).vin == VIN


@UUID_IN_RAW_SQL
def test_a_live_record_taking_the_vin_in_the_race_is_named(db_session, monkeypatch):
    car_id, duplicate = _car_and_duplicate(db_session)
    taken: list[str] = []

    def _write(session: Session) -> None:
        _move_vin(session, duplicate.id, SLIPPED_VIN)
        taken.append(create_vehicle_mdm(session, vin=RIGHT_VIN, catalogue_variant_id=None).vehicle_number)

    _race(db_session, monkeypatch, _write)

    with pytest.raises(ConflictError) as refused:
        _correct_directly(db_session, car_id)

    assert refused.value.details == {"reason": "vin_belongs_to_other_vehicle", "vehicleNumber": taken[0]}


# --- 2. two chassis-number records ------------------------------------------------

RIGHT_CHASSIS = "1130101234599"


def _chassis(db_session, number: str) -> VehicleMdm:
    return create_vehicle_mdm(
        db_session,
        vin=number,
        catalogue_variant_id=None,
        first_registration_date=PRE_1981,
        chassis_make_key="mercedesbenz",
    )


def _correct_chassis(db_session, car_id: uuid.UUID, *, old: str, new: str, compensation: bool = False):
    return correct_vehicle_vin(
        db_session,
        vehicle_id=car_id,
        old_vin=old,
        new_vin=new,
        reason=REASON,
        actor_id=None,
        tenant_id=uuid.uuid4(),
        compensation=compensation,
    )


def test_a_chassis_number_record_takes_the_number_over_from_its_own_merged_duplicate(db_session):
    car = _chassis(db_session, CHASSIS)
    db_session.commit()
    duplicate = _chassis(db_session, RIGHT_CHASSIS)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=car.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())

    corrected = _correct_chassis(db_session, car.id, old=CHASSIS, new=RIGHT_CHASSIS)

    assert (corrected.vin, corrected.record_changed, corrected.previous_chassis_make_key) == (RIGHT_CHASSIS, True, None)
    db_session.expire_all()
    survivor, dead = db_session.get(VehicleMdm, car.id), db_session.get(VehicleMdm, duplicate.id)
    assert (survivor.vin, survivor.identifier_kind, survivor.chassis_make_key) == (
        RIGHT_CHASSIS,
        VehicleIdentifierKind.CHASSIS_NUMBER,
        "mercedesbenz",
    )
    assert (dead.vin, dead.identifier_kind, dead.chassis_make_key, dead.merged_into_vehicle_id) == (
        CHASSIS,
        VehicleIdentifierKind.CHASSIS_NUMBER,
        "mercedesbenz",
        car.id,
    )

    # The compensation (the two numbers swapped) swaps them back.
    _correct_chassis(db_session, car.id, old=RIGHT_CHASSIS, new=CHASSIS, compensation=True)

    db_session.expire_all()
    assert db_session.get(VehicleMdm, car.id).vin == CHASSIS
    assert db_session.get(VehicleMdm, duplicate.id).vin == RIGHT_CHASSIS


# --- 3. no lock cycle with a re-merge of the pair ----------------------------------


@ROW_LOCKS
def test_a_re_merge_of_the_pair_is_refused_without_waiting_for_the_survivor(db_session):
    """A correction holds the survivor's row lock and goes for its duplicate's.
    The re-merge must not take the duplicate's lock and then wait for the
    survivor's — it is refused before it locks anything."""

    car_id, duplicate = _car_and_duplicate(db_session)
    versions = (duplicate.version, db_session.get(VehicleMdm, car_id).version)
    db_session.commit()
    with db_session.get_bind().connect() as correction:
        correction.execute(text("SELECT id FROM vehicle_mdm WHERE id = :id FOR NO KEY UPDATE"), {"id": car_id})
        db_session.execute(text("SET LOCAL lock_timeout = '2s'"))
        try:
            with pytest.raises(ConflictError) as refused:
                merge_vehicle_records(
                    db_session,
                    duplicate_id=duplicate.id,
                    survivor_id=car_id,
                    duplicate_version=versions[0],
                    survivor_version=versions[1],
                    actor_id=uuid.uuid4(),
                    tenant_id=uuid.uuid4(),
                )
        except OperationalError:  # pragma: no cover - the defect: it waited for the lock
            pytest.fail("the re-merge waited for the survivor's lock")
        finally:
            db_session.rollback()
            correction.rollback()

    assert refused.value.details["reason"] == "vehicle_merged"
    assert refused.value.details["vehicleId"] == str(duplicate.id)
