"""KAN-663: the trade-in purchase (KAN-189) and its returned storno (KAN-572)
lock the party rows they write in id order, across owner and keeper, so
neither deadlocks against the label refresh (KAN-186), which locks a
vehicle's party rows in id order.

Deterministic, in the style of
test_kan644_kan583_handover.py::test_a_cancel_or_a_moved_start_never_deadlocks_against_the_label_refresh:
the «refresh» holds the older row, the writer starts and must wait on it, then
the refresh reaches for the newer row. A writer that took the newer row first
deadlocks there (Postgres aborts one side); one that locks in id order waits
holding nothing, and the refresh goes through.
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.trade_in_holders import (
    record_former_holder_on_purchase,
    reopen_former_holder_after_storno,
)
from app.customer.services.vehicle_party import allocate_vehicle_party
from tests.test_customer_vehicle_party_close_rule import _wait_until_a_backend_waits_on_a_lock
from tests.test_customer_vehicle_party_future_holder import (  # noqa: F401 — _never_inverted is an autouse fixture
    _customer,
    _never_inverted,
    _vehicle,
)

LOCK_ROW = "SELECT 1 FROM vehicle_party WHERE id = :id FOR NO KEY UPDATE"


def _allocate(db, vehicle_id, customer, group_id, role: VehiclePartyRole, *, days_ago: int) -> VehicleParty:
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=customer.id, role=role, group_id=group_id, actor_id=uuid.uuid4(),
        effective_from=(utcnow() - dt.timedelta(days=days_ago)).replace(microsecond=0),
    )


def _race(engine, *, older_id, newer_id, write) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _write():
        with factory() as s:
            try:
                s.execute(text("SET LOCAL lock_timeout = '10s'"))
                write(s)
                s.commit()
            except Exception as exc:  # noqa: BLE001 — collected and asserted empty
                errors.append(exc)
                s.rollback()

    with factory() as refresh:
        refresh.execute(text("SET LOCAL lock_timeout = '10s'"))
        refresh.execute(text(LOCK_ROW), {"id": older_id})  # the refresh holds the older row
        writer = threading.Thread(target=_write)
        writer.start()
        try:
            _wait_until_a_backend_waits_on_a_lock(engine)  # the writer waits on the older row
            refresh.execute(text(LOCK_ROW), {"id": newer_id})  # …and the refresh reaches for the newer one
            refresh.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            refresh.rollback()
        writer.join(timeout=30)

    assert not writer.is_alive()
    assert errors == []


@pytest.fixture
def _postgres_only(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("row locks are Postgres-only; the SQLite lane serialises writers itself")


def test_recording_the_former_holder_never_deadlocks_against_the_label_refresh(engine, db_session, _postgres_only):
    """The purchase used to lock the customer's keeper rows, then its owner
    rows. Clara became owner first (older row) and keeper later (newer row):
    it took the newer row and waited for the older one."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW663001")
    clara = _customer(db_session, group_id, "Clara")
    owner = _allocate(db_session, vehicle.id, clara, group_id, VehiclePartyRole.OWNER, days_ago=30)
    keeper = _allocate(db_session, vehicle.id, clara, group_id, VehiclePartyRole.KEEPER, days_ago=30)
    db_session.commit()
    assert owner.id < keeper.id
    ended_at = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)

    _race(
        engine, older_id=owner.id, newer_id=keeper.id,
        write=lambda s: record_former_holder_on_purchase(
            s, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
            # Signed when Clara's rows start, so the purchase only closes them
            # (KAN-566 would fill a stretch before them otherwise).
            held_from=max(owner.effective_from, keeper.effective_from), ended_at=ended_at, actor_id=None,
        ),
    )

    db_session.expire_all()
    assert {row.effective_to for row in db_session.scalars(select(VehicleParty)).all()} == {ended_at}


def test_reopening_after_a_returned_storno_never_deadlocks_against_the_label_refresh(engine, db_session, _postgres_only):
    """The reopen used to lock the customer's closed row, then the role's
    other holders. A leasing keeper recorded before the trade-in holds the
    older row: it took Clara's newer row and waited for the leasing one."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW663002")
    clara, leasing = _customer(db_session, group_id, "Clara"), _customer(db_session, group_id, "Leasing", "AG")
    leasing_keeper = _allocate(db_session, vehicle.id, leasing, group_id, VehiclePartyRole.KEEPER, days_ago=60)
    db_session.commit()
    ended_at = (utcnow() - dt.timedelta(days=1)).replace(microsecond=0)
    signed_at = ended_at - dt.timedelta(days=30)
    record_former_holder_on_purchase(
        db_session, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
        held_from=signed_at, ended_at=ended_at, actor_id=None,
    )
    db_session.commit()
    clara_keeper = db_session.scalars(
        select(VehicleParty).where(VehicleParty.customer_id == clara.id, VehicleParty.role == VehiclePartyRole.KEEPER)
    ).one()
    assert leasing_keeper.id < clara_keeper.id
    storno_at = utcnow()

    _race(
        engine, older_id=leasing_keeper.id, newer_id=clara_keeper.id,
        write=lambda s: reopen_former_holder_after_storno(
            s, vehicle_id=vehicle.id, customer_id=clara.id, group_id=group_id,
            closed_after=signed_at, closed_until=storno_at, actor_id=None,
        ),
    )

    db_session.expire_all()
    assert db_session.get(VehicleParty, clara_keeper.id).effective_to is None  # Clara holds it again
    leasing_end = db_session.get(VehicleParty, leasing_keeper.id).effective_to
    assert leasing_end is not None and leasing_end <= utcnow()  # the close rule ended the other keeper
