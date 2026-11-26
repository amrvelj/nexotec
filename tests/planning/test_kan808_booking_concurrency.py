"""KAN-808 PR-1c (ADR-221 (1)): booking writes of one Betrieb are serialised
by its advisory lock, so two sessions booking one workplace for two cars at
once end in one booking and one 409 — never two bookings, as two overlap
reads under READ COMMITTED would allow without the lock. Postgres only: the
lock is a no-op on SQLite, whose one shared connection has no second
session."""

import threading
import time

from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.planning.models.booking import Booking
from app.planning.services import bookings
from tests import planning_support as p
from tests.postgres_only import ROW_LOCKS


def _wait_until_a_backend_waits_on_a_lock(engine, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            if waiting:
                return
            watcher.rollback()
            time.sleep(0.05)
    raise AssertionError("the second booking never waited for the first one's lock")


@ROW_LOCKS
def test_two_cars_booked_onto_one_workplace_at_once_are_one_booking_and_one_conflict(engine, client, db_session, monkeypatch):
    shop = p.workshop(client, db_session)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    outcome: list[str] = []
    first_holds_the_lock = threading.Event()
    release_the_first = threading.Event()

    finish = bookings._finish

    def held_finish(db, **kwargs):
        # The first write has judged and written, not yet committed: it holds the lock.
        if threading.current_thread().name == "first":
            first_holds_the_lock.set()
            assert release_the_first.wait(30)
        return finish(db, **kwargs)

    monkeypatch.setattr(bookings, "_finish", held_finish)

    def book(name: str) -> None:
        session = factory()
        try:
            p.work(session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
            outcome.append("booked")
        except ConflictError as refused:
            outcome.append((refused.details or {}).get("reason", "conflict"))
        except BaseException as exc:  # noqa: BLE001 - reported below
            outcome.append(type(exc).__name__)
        finally:
            session.close()

    first = threading.Thread(target=book, args=("first",), name="first")
    second = threading.Thread(target=book, args=("second",), name="second")
    first.start()
    try:
        assert first_holds_the_lock.wait(30)
        second.start()
        _wait_until_a_backend_waits_on_a_lock(engine)
    finally:
        release_the_first.set()
        first.join(timeout=30)
        if second.ident is not None:
            second.join(timeout=30)

    assert sorted(outcome) == ["booked", "planning_workplace_taken"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Booking)) == 1
