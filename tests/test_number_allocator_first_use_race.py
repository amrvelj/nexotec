"""KAN-70 (G-72): two callers racing the very first allocation for a fresh
counter key both get a number, and neither raises.

The race, made deterministic: the first session inserts the counter row and
holds its transaction open; the second session cannot see that row, so it
tries to insert the same key and blocks on the primary key. When the first
commits, the second's INSERT fails with a UniqueViolation — before the fix,
an uncaught IntegrityError and so an unhandled 500. After it, the second
re-reads the winner's row under its lock and takes the next number.

Postgres only: on SQLite `FOR UPDATE` is a no-op and writers serialise on the
whole database, so the race cannot happen there and cannot be observed.
"""

import os
import threading
import time
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.customer.services.customer import _allocate_customer_number
from app.inventory.services.stock_item import allocate_stock_number
from app.sales.services.numbering import allocate_contract_number, allocate_offer_number
from app.valuation.services.valuation import allocate_valuation_number
from app.vehicle.services.vehicle_mdm import allocate_vehicle_number

_KEY = uuid.UUID("01900000-0000-7000-8000-000000000070")

ALLOCATORS: dict[str, tuple[Callable[[Session], str], str]] = {
    "sales-offer": (lambda db: allocate_offer_number(db, _KEY), "O"),
    "sales-contract": (lambda db: allocate_contract_number(db, _KEY), "C"),
    "customer": (lambda db: _allocate_customer_number(db, _KEY), "K"),
    "vehicle": (allocate_vehicle_number, "F"),
    "stock-item": (lambda db: allocate_stock_number(db, _KEY), "S"),
    "valuation": (lambda db: allocate_valuation_number(db, _KEY), "B"),
}


def _wait_for_a_blocked_lock(engine, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            # The loser waits on the winner's transactionid lock, which
            # pg_locks lists without a database — so ask pg_stat_activity.
            waiting = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity "
                     "WHERE datname = current_database() AND wait_event_type = 'Lock'")
            ).scalar_one()
            if waiting:
                return True
            conn.rollback()
            time.sleep(0.05)
    return False


@pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="FOR UPDATE is a no-op on SQLite; the first-use race is Postgres-only",
)
@pytest.mark.parametrize("name", ALLOCATORS)
def test_first_allocation_for_a_fresh_key_survives_a_concurrent_caller(engine, name):

    allocate, prefix = ALLOCATORS[name]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    second_result: list[str] = []
    second_errors: list[BaseException] = []

    def _second() -> None:
        with factory() as session:
            try:
                second_result.append(allocate(session))
                session.commit()
            except BaseException as exc:  # noqa: BLE001 — collected and asserted empty below, never swallowed
                second_errors.append(exc)

    with factory() as first_session:
        first_number = allocate(first_session)  # inserts the counter row, transaction still open
        second = threading.Thread(target=_second, name="second")
        second.start()
        try:
            # The second caller must be parked on the first's uncommitted row
            # before the first commits, or this is not the race at all.
            assert _wait_for_a_blocked_lock(engine), "the second allocation never blocked on the first's insert"
        finally:
            first_session.commit()
            second.join(timeout=10)

    assert not second.is_alive()
    assert second_errors == []
    assert first_number == f"{prefix}-000001"
    assert second_result == [f"{prefix}-000002"]


@pytest.mark.parametrize("name", ALLOCATORS)
def test_steady_state_rollback_still_refunds_the_number(engine, name):
    """G-71's behaviour, unchanged: once the counter row exists, a rolled-back
    allocation hands its number back, and the next caller receives it."""

    allocate, prefix = ALLOCATORS[name]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        assert allocate(session) == f"{prefix}-000001"
        session.commit()
    with factory() as session:
        assert allocate(session) == f"{prefix}-000002"
        session.rollback()
    with factory() as session:
        assert allocate(session) == f"{prefix}-000002"
        session.commit()


@pytest.mark.parametrize("name", ALLOCATORS)
def test_the_callers_own_integrity_error_is_not_swallowed_on_first_use(engine, name):
    """The first-use savepoint catches IntegrityError, but only the counter
    row's own. A caller's pending row that breaks a constraint must still
    surface as an IntegrityError (which callers map to 409), not be eaten
    and turned into a PendingRollbackError (a 500)."""

    from app.core.uuid7 import uuid7
    from app.platform.models.reference_data import ReferenceList

    allocate, _ = ALLOCATORS[name]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add(ReferenceList(id=uuid7(), list_code="country"))  # seeded by conftest: a duplicate
        with pytest.raises(IntegrityError):
            allocate(session)
        session.rollback()
