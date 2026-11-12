"""KAN-337 — `vehicle.public.resolve_vehicle_mdm_by_vin`, the one way another
context turns a VIN into its vehicle-mdm record.

The KAN-257 promotion tests (`test_inventory_vin_resolution.py`) prove it
through Stock's purchase path; these prove the public function's own
contract, whoever calls it: it works on a session of Vehicle's own (the
caller's pending work is neither committed nor rolled back), it finds a
known VIN and creates an unknown one, a lost insert race returns the
winner's record, any other conflict still reaches the caller, and it hands
back plain data, never Vehicle's ORM row.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.platform.models.dealership import DealerGroup
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import ResolvedVehicleMdm, create_or_get_vehicle_mdm, resolve_vehicle_mdm_by_vin
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION

VIN = "WVWZZZ1KZAW337001"

pytestmark = NEEDS_ITS_OWN_CONNECTION


def _count(engine, vin: str) -> int:
    with Session(bind=engine) as fresh:
        return fresh.scalar(select(func.count()).select_from(VehicleMdm).where(VehicleMdm.vin == vin))


def _pending_group(db_session) -> DealerGroup:
    """Some pending work of the caller's own, flushed but not committed."""

    group = DealerGroup(name=f"Pending {uuid.uuid4()}")
    db_session.add(group)
    db_session.flush()
    return group


def _group_committed(engine, group_id: uuid.UUID) -> bool:
    with Session(bind=engine) as fresh:
        return fresh.get(DealerGroup, group_id) is not None


def test_an_unknown_vin_is_created_and_committed_without_committing_the_callers_work(db_session, engine):
    group = _pending_group(db_session)

    resolved = resolve_vehicle_mdm_by_vin(db_session, vin=VIN, actor_id=uuid.uuid4())

    assert isinstance(resolved, ResolvedVehicleMdm)
    assert not isinstance(resolved, VehicleMdm)
    assert resolved.vin == VIN
    assert _count(engine, VIN) == 1  # committed on Vehicle's session
    assert not _group_committed(engine, group.id)  # the caller's work is still only pending
    db_session.rollback()
    assert not _group_committed(engine, group.id)
    assert _count(engine, VIN) == 1  # the caller's rollback leaves Vehicle's commit alone


def test_a_known_vin_returns_its_record_and_creates_nothing(db_session, engine):
    known, _created = create_or_get_vehicle_mdm(db_session, vin=VIN)

    resolved = resolve_vehicle_mdm_by_vin(db_session, vin=VIN)

    assert resolved == ResolvedVehicleMdm(id=known.id, vin=VIN)
    assert _count(engine, VIN) == 1


def test_the_record_it_creates_names_the_actor(db_session, engine):
    actor_id = uuid.uuid4()

    resolved = resolve_vehicle_mdm_by_vin(db_session, vin=VIN, actor_id=actor_id)

    with Session(bind=engine) as fresh:
        record = fresh.get(VehicleMdm, resolved.id)
        assert record is not None
        assert record.created_by == actor_id


def test_a_lost_insert_race_returns_the_winners_record_and_keeps_the_callers_work(db_session, engine, monkeypatch):
    group = _pending_group(db_session)
    real_lookup = vehicle_mdm_service.get_vehicle_mdm_by_vin
    winners: list[uuid.UUID] = []

    def lookup(db, vin):
        if not winners:
            with Session(bind=engine, autoflush=False, expire_on_commit=False) as other:
                winners.append(vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None).id)
            return None
        return real_lookup(db, vin)

    monkeypatch.setattr(vehicle_mdm_service, "get_vehicle_mdm_by_vin", lookup)

    resolved = resolve_vehicle_mdm_by_vin(db_session, vin=VIN)

    assert resolved == ResolvedVehicleMdm(id=winners[0], vin=VIN)
    assert _count(engine, VIN) == 1
    # The guard's rollback hit Vehicle's session, never the caller's.
    assert db_session.get(DealerGroup, group.id) is not None
    db_session.commit()
    assert _group_committed(engine, group.id)


def test_a_conflict_that_is_not_the_insert_race_reaches_the_caller(db_session, monkeypatch):
    refusal = ConflictError("Something else.", details={"vin": VIN, "reason": "something_else"})

    def refuse(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(vehicle_mdm_service, "create_or_get_vehicle_mdm", refuse)

    with pytest.raises(ConflictError) as raised:
        resolve_vehicle_mdm_by_vin(db_session, vin=VIN)

    assert raised.value is refusal
