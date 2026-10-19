"""KAN-380: two simultaneous first grants for the same (user, target).

A first grant reads the existing row, finds none and inserts one. Two at once
both found none; the second insert hit the unique constraint at an unguarded
flush and the request returned 500. The membership POST and the
text-delegation PUT now lock the user row before they read (as the
entitlement PUT locks the dealership row), so the second waits for the first
and is then refused like any later grant: 409, never a 500.
"""

import threading
import uuid

from sqlalchemy import func, select

from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.services import entitlement as entitlement_service
from app.platform.services import membership as membership_service
from app.platform.services import text_delegation as text_delegation_service
from tests.membership_support import create_dealership, create_user, grant, platform_admin
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock


def _pause_the_first_audit_row(monkeypatch, module):
    """Pauses the first request at its first audit row — after its new row
    reached the database, before it commits. Requests run on the TestClient's
    worker threads, so the first call is the one paused, not a named thread."""

    reached, release = threading.Event(), threading.Event()
    claimed = threading.Lock()
    real_record = module.record_audit_event

    def _record(db, **kwargs):
        if claimed.acquire(blocking=False):
            reached.set()
            release.wait(timeout=10)
        return real_record(db, **kwargs)

    monkeypatch.setattr(module, "record_audit_event", _record)
    return reached, release


def _race(engine, monkeypatch, module, send):
    """Runs `send` twice at once: the first is paused holding its new row,
    the second starts and must wait on a lock. Returns both status codes."""

    reached, release = _pause_the_first_audit_row(monkeypatch, module)
    errors: list[Exception] = []
    codes: list[int] = []
    first = _run("first-grant", errors, lambda: codes.append(send().status_code))
    try:
        assert reached.wait(timeout=10), "the first grant never reached its audit row"
        second = _run("second-grant", errors, lambda: codes.append(send().status_code))
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
    finally:
        release.set()
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    return sorted(codes)


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def test_two_first_membership_grants_at_once_are_a_201_and_a_409(engine, client, db_session, monkeypatch):
    _postgres_only(engine)
    home = create_dealership(client, "Garage Nord AG")
    sister = create_dealership(client, "Garage Süd AG", group_of=home)
    user = create_user(client, home)

    codes = _race(engine, monkeypatch, membership_service, lambda: grant(client, home, user, sister))

    assert codes == [201, 409]
    assert _count(
        db_session,
        DealershipMembership,
        DealershipMembership.user_id == uuid.UUID(user["id"]),
        DealershipMembership.dealership_id == uuid.UUID(sister),
    ) == 1


def test_two_first_text_delegation_puts_at_once_are_a_200_and_a_409(engine, client, db_session, monkeypatch):
    _postgres_only(engine)
    dealership = create_dealership(client)
    user = create_user(client, dealership, is_dealer_manager=False)

    def _put():
        return client.put(
            f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations",
            json={"categories": ["email"]},
            headers={**platform_admin(), "If-Match": "1"},
        )

    codes = _race(engine, monkeypatch, text_delegation_service, _put)

    assert codes == [200, 409]
    assert _count(db_session, StandardTextDelegation, StandardTextDelegation.user_id == uuid.UUID(user["id"])) == 1


def test_two_first_entitlement_puts_at_once_are_a_200_and_a_409(engine, client, db_session, monkeypatch):
    """Already serialised by the dealership row lock (KAN-198); pinned here
    with the other two first grants."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    version = client.get(f"/v1/dealerships/{dealership}", headers=platform_admin()).json()["version"]

    def _put():
        return client.put(
            f"/v1/dealerships/{dealership}/entitlements",
            json={"modules": ["sales"]},
            headers={**platform_admin(), "If-Match": str(version)},
        )

    codes = _race(engine, monkeypatch, entitlement_service, _put)

    assert codes == [200, 409]
    assert _count(db_session, ModuleEntitlement, ModuleEntitlement.dealership_id == uuid.UUID(dealership)) == 1
