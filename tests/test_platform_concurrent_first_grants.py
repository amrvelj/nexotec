"""KAN-380: two simultaneous first grants for the same (user, target).

A first grant reads the existing row, finds none and inserts one. Two at once
both found none; the second insert hit the unique constraint at an unguarded
flush and the request returned 500. The membership POST and the
text-delegation PUT now lock the user row before they read (as the
entitlement PUT locks the dealership row), so the second waits for the first
and is then refused like any later grant: 409, never a 500.
"""

import inspect
import threading
import uuid

import pytest
from sqlalchemy import func, select

from app.platform.api import dealerships as dealerships_api
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.standard_text_delegation import StandardTextDelegation
from app.platform.services import auth_sessions
from app.platform.services import entitlement as entitlement_service
from app.platform.services import membership as membership_service
from app.platform.services import text_delegation as text_delegation_service
from tests.membership_support import create_dealership, create_user, grant, if_match, platform_admin
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


def _race(engine, monkeypatch, module, send, then=None):
    """Runs `send` and then `then` (default: `send` again) at once: the first
    is paused holding the rows it wrote, the second starts and must wait on a
    lock. Returns both status codes."""

    reached, release = _pause_the_first_audit_row(monkeypatch, module)
    errors: list[Exception] = []
    codes: list[int] = []
    second_send = then or send
    first = _run("first-grant", errors, lambda: codes.append(send().status_code))
    try:
        assert reached.wait(timeout=10), "the first grant never reached its audit row"
        second = _run("second-grant", errors, lambda: codes.append(second_send().status_code))
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


def test_a_delegation_put_racing_a_delegation_delete_is_a_409_never_a_deadlock(engine, client, db_session, monkeypatch):
    """Every route that writes a user's delegations or memberships takes the
    user row lock first. A DELETE that locked the delegation rows first and
    the user row last (at its version bump) deadlocked against a PUT holding
    the user row and waiting for a delegation row: a 500."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    user = create_user(client, dealership, is_dealer_manager=False)
    path = f"/v1/dealerships/{dealership}/users/{user['id']}/text-delegations"
    granted = client.put(path, json={"categories": ["email"]}, headers={**platform_admin(), "If-Match": "1"})
    assert granted.status_code == 200, granted.text

    def _delete():
        return client.delete(path, headers={**platform_admin(), "If-Match": "2"})

    def _put():
        return client.put(path, json={"categories": ["document"]}, headers={**platform_admin(), "If-Match": "2"})

    codes = _race(engine, monkeypatch, text_delegation_service, _delete, then=_put)

    assert codes == [204, 409]
    listed = client.get(path, headers=platform_admin()).json()
    assert listed == {"categories": [], "userVersion": 3}


def test_a_membership_grant_racing_a_membership_end_is_a_201_and_a_204_never_a_deadlock(
    engine, client, db_session, monkeypatch
):
    """The grant holds the user row and waits to revoke the user's sessions;
    an end that locked the session rows first and the user row last (its
    version bump) deadlocked against it: a 500. Both take the user row first
    now, so the end waits and then succeeds."""

    _postgres_only(engine)
    home = create_dealership(client, "Garage Nord AG")
    sister_a = create_dealership(client, "Garage Süd AG", group_of=home)
    sister_b = create_dealership(client, "Garage West AG", group_of=home)
    user = create_user(client, home, is_dealer_manager=False)
    assert grant(client, home, user, sister_a).status_code == 201
    auth_sessions.create(
        db_session, user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(home), device_summary="test",
        ip_address="127.0.0.1", amr=["pwd"],
    )
    db_session.commit()
    headers = if_match(client, home, user)

    def _end():
        return client.delete(f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister_a}", headers=headers)

    codes = _race(engine, monkeypatch, membership_service, lambda: grant(client, home, user, sister_b), then=_end)

    assert codes == [201, 204]


@pytest.mark.parametrize(
    "route",
    [
        "update_user",
        "grant_membership",
        "end_membership",
        "set_membership_manager_flag",
        "set_home_manager_flag",
        "set_text_delegations",
        "end_text_delegations",
        # KAN-200: the deactivation step, the hand-over and the signature image.
        "deactivate_user",
        "transfer_user_ownership",
        "put_user_signature",
        "delete_user_signature",
    ],
)
def test_every_user_administration_write_takes_the_user_row_lock_first(route):
    """One lock order for every route that writes a user's memberships,
    delegations or the user row (KAN-380): the user row, then the child rows.
    A route on the plain read locks them the other way round and deadlocks
    against the others — only a few of those pairs have a race test."""

    source = inspect.getsource(getattr(dealerships_api, route))
    assert "get_user_for_update_or_404(" in source
    assert "get_user_or_404(" not in source

