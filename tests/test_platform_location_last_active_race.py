"""KAN-519 (2): two concurrent deactivations of a Betrieb's last two active
locations — exactly one wins (FR-A-03: a Betrieb always keeps an active
location).

`location._assert_not_last_active` takes the dealership row lock before it
counts the other active sites, so the second request waits for the first,
then counts under READ COMMITTED and sees the first one's site inactive. The
test pauses the first deactivation after its check, holding the lock, starts
the second on a connection of its own, waits until Postgres reports it
blocked on a lock, and only then lets the first commit.
"""

import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.platform.models.dealership import Location
from app.platform.schemas.location import LocationUpdate
from app.platform.services import location as location_service
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock
from tests.test_dealership import _create_dealership
from tests.test_kan204_locations import _create, _list

_FIRST = "deactivate-first"


def _deactivate(db, location_id: uuid.UUID) -> None:
    location = db.get(Location, location_id)
    assert location is not None
    location_service.update_location(db, location=location, data=LocationUpdate(is_active=False), actor_id=uuid.uuid4())


def test_two_concurrent_deactivations_of_the_last_two_active_locations_cannot_both_win(
    engine, client, db_session, monkeypatch
):
    _postgres_only(engine)
    dealership = _create_dealership(client)
    [seat] = _list(client, dealership["id"])
    branch = _create(client, dealership["id"])
    seat_id, branch_id = uuid.UUID(seat["id"]), uuid.UUID(branch["id"])

    reached, release = threading.Event(), threading.Event()
    real_check = location_service._assert_not_last_active

    def _check_then_pause(db, *, location):
        real_check(db, location=location)
        if threading.current_thread().name == _FIRST:
            reached.set()
            release.wait(timeout=10)

    monkeypatch.setattr(location_service, "_assert_not_last_active", _check_then_pause)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    refusals: list[ConflictError] = []

    def _first():
        with factory() as session:
            _deactivate(session, seat_id)

    def _second():
        with factory() as session:
            try:
                _deactivate(session, branch_id)
            except ConflictError as exc:
                refusals.append(exc)

    first = _run(_FIRST, errors, _first)
    try:
        assert reached.wait(timeout=10), "the first deactivation never reached its pause"
        second = _run("deactivate-second", errors, _second)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the second waits on the dealership row
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
    finally:
        release.set()

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert [r.details["reason"] for r in refusals] == ["last_active_location"]
    db_session.expire_all()
    active = db_session.scalars(
        select(Location.id).where(Location.tenant_id == uuid.UUID(dealership["id"]), Location.is_active.is_(True))
    ).all()
    assert active == [branch_id]
