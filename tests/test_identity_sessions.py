"""KAN-196 — sessions (FR-AU-17…19): one `session` row per login that every
request is checked against; two hours idle, absolute end at the next midnight
in Europe/Zurich (Anto, 2026-10-17); revocation by the user, a manager, a
reset or a status change takes effect on the next request; logout ends the
row; switch-dealership keeps it.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token, decode_token_claims
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.auth_session import AuthSession
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.services import auth_sessions
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
)

SESSION_ENDED = "Your session has ended — sign in again."


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _row(db_session, token: str) -> AuthSession:
    db_session.expire_all()
    row = db_session.get(AuthSession, uuid.UUID(decode_token_claims(token)["sid"]))
    assert row is not None
    return row


def test_a_login_creates_the_session_row_the_token_names(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)

    claims = decode_token_claims(token)
    row = _row(db_session, token)
    assert str(row.user_id) == anna["id"]
    assert str(row.dealership_id) == dealership
    assert claims["amr"] == ["pwd"] == row.amr
    assert claims["mfa"] is False
    assert claims["exp"] == int(row.absolute_expires_at.timestamp())
    assert row.idle_expires_at - row.created_at == auth_sessions.idle_timeout()
    assert row.device_summary.startswith("testclient")
    assert row.revoked_at is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (dt.datetime(2026, 10, 17, 10, 0, tzinfo=dt.UTC), dt.datetime(2026, 10, 17, 22, 0, tzinfo=dt.UTC)),  # CEST
        (dt.datetime(2026, 12, 1, 10, 0, tzinfo=dt.UTC), dt.datetime(2026, 12, 1, 23, 0, tzinfo=dt.UTC)),  # CET
        (dt.datetime(2026, 10, 17, 22, 30, tzinfo=dt.UTC), dt.datetime(2026, 10, 18, 22, 0, tzinfo=dt.UTC)),
        (dt.datetime(2026, 10, 24, 23, 30, tzinfo=dt.UTC), dt.datetime(2026, 10, 25, 23, 0, tzinfo=dt.UTC)),  # DST ends
    ],
)
def test_the_absolute_end_is_the_next_midnight_in_zurich(now, expected):
    assert auth_sessions.absolute_expiry(now) == expected


def test_a_revoked_session_is_refused_on_its_next_request(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 200

    auth_sessions.revoke(db_session, _row(db_session, token), reason="test")
    db_session.commit()

    response = client.get("/v1/auth/me", headers=cookie_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["message"] == SESSION_ENDED
    assert client.get(f"/v1/dealerships/{dealership}", headers=cookie_headers(token)).status_code == 401


def test_an_idle_session_expires_and_activity_extends_it(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    row = _row(db_session, token)
    now = dt.datetime.now(dt.UTC)
    row.last_seen_at = now - dt.timedelta(minutes=30)
    row.idle_expires_at = now + dt.timedelta(minutes=90)
    db_session.commit()

    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 200
    row = _row(db_session, token)
    assert row.last_seen_at > now - dt.timedelta(seconds=5)
    # Extended by the idle timeout, but never past the absolute end.
    assert row.idle_expires_at >= min(now + dt.timedelta(minutes=115), row.absolute_expires_at - dt.timedelta(seconds=5))

    row.idle_expires_at = now - dt.timedelta(seconds=1)
    db_session.commit()
    response = client.get("/v1/auth/me", headers=cookie_headers(token))
    assert response.status_code == 401
    assert response.json()["error"]["message"] == SESSION_ENDED


def test_a_session_past_its_absolute_end_is_refused_even_when_active(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    row = _row(db_session, token)
    row.absolute_expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    row.idle_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    db_session.commit()

    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 401


def test_logout_ends_the_row_and_works_for_a_session_already_ended(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)

    response = client.post("/v1/auth/logout", headers=cookie_headers(token))
    assert response.status_code == 200, response.text
    row = _row(db_session, token)
    assert row.revoked_at is not None
    assert row.revoked_reason == "logout"
    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 401
    # A second logout with the dead cookie is not an error: it clears the cookie.
    response = client.post("/v1/auth/logout", headers=cookie_headers(token))
    assert response.status_code == 200
    assert (
        "Max-Age=0" in response.headers.get("set-cookie", "")
        or "expires=" in response.headers.get("set-cookie", "").lower()
    )


def test_switching_dealership_keeps_the_session(client, login, db_session):
    dealer_a = create_dealership(client)
    dealer_b = create_dealership(client)
    anna = create_user(client, dealer_a)
    db_session.add(DealershipMembership(user_id=uuid.UUID(anna["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()
    token = login.session_cookie(anna)

    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": dealer_b}, headers=cookie_headers(token))
    assert response.status_code == 200, response.text
    switched = response.cookies.get("dms_session")
    assert decode_token_claims(switched)["sid"] == decode_token_claims(token)["sid"]
    live = db_session.scalars(
        select(AuthSession).where(AuthSession.user_id == uuid.UUID(anna["id"]), AuthSession.revoked_at.is_(None))
    ).all()
    assert [str(row.id) for row in live] == [decode_token_claims(token)["sid"]]


def test_a_user_lists_and_ends_their_own_sessions(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    first = login.session_cookie(anna)
    second = login.session_cookie(anna)
    beat = create_user(client, dealership, isDealerManager=False)
    beats = login.session_cookie(beat)

    listing = client.get("/v1/auth/sessions", headers=cookie_headers(first))
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 2
    assert [item["current"] for item in items].count(True) == 1
    other = next(item for item in items if not item["current"])
    assert other["id"] == decode_token_claims(second)["sid"]

    # Not another user's session: 404, never 403.
    foreign = decode_token_claims(beats)["sid"]
    assert client.delete(f"/v1/auth/sessions/{foreign}", headers=cookie_headers(first)).status_code == 404
    assert client.get("/v1/auth/me", headers=cookie_headers(beats)).status_code == 200

    assert client.delete(f"/v1/auth/sessions/{other['id']}", headers=cookie_headers(first)).status_code == 204
    assert client.get("/v1/auth/me", headers=cookie_headers(second)).status_code == 401
    assert client.get("/v1/auth/me", headers=cookie_headers(first)).status_code == 200
    assert len(client.get("/v1/auth/sessions", headers=cookie_headers(first)).json()["items"]) == 1


def test_a_manager_ends_every_session_of_a_user(client, login, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    beat = create_user(client, dealership, isDealerManager=False)
    managers = login.session_cookie(manager)
    one = login.session_cookie(beat)
    two = login.session_cookie(beat)

    response = client.delete(
        f"/v1/dealerships/{dealership}/users/{beat['id']}/sessions", headers=cookie_headers(managers)
    )
    assert response.status_code == 204, response.text

    assert client.get("/v1/auth/me", headers=cookie_headers(one)).status_code == 401
    assert client.get("/v1/auth/me", headers=cookie_headers(two)).status_code == 401
    assert client.get("/v1/auth/me", headers=cookie_headers(managers)).status_code == 200
    events = db_session.scalars(select(AuthEvent).where(AuthEvent.kind == AuthEventKind.SESSION_REVOKED)).all()
    (event,) = [e for e in events if e.details["reason"] == "manager"]
    assert str(event.user_id) == beat["id"]
    assert str(event.actor_id) == manager["id"]
    assert event.details == {"count": 2, "reason": "manager"}

    # Not a manager: 403. Another tenant's user: 404.
    assert (
        client.delete(
            f"/v1/dealerships/{dealership}/users/{manager['id']}/sessions",
            headers=cookie_headers(login.session_cookie(beat)),
        ).status_code
        == 403
    )
    other = create_dealership(client)
    assert (
        client.delete(
            f"/v1/dealerships/{other}/users/{beat['id']}/sessions", headers=cookie_headers(managers)
        ).status_code
        == 404
    )


def test_a_role_or_flag_change_ends_the_users_sessions(client, login, db_session):
    """Roles, the manager flag and memberships are claims the token carries
    for the session's day; a change ends the sessions so the next sign-in
    carries the new set (the KAN-198 precedent for entitlements)."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    managers = login.session_cookie(manager)
    annas = login.session_cookie(anna)

    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{anna['id']}",
        json={"accessRoles": ["sales", "aftersales"]},
        headers={**cookie_headers(managers), "If-Match": "2"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/v1/auth/me", headers=cookie_headers(annas)).status_code == 401
    assert client.get("/v1/auth/me", headers=cookie_headers(managers)).status_code == 200

    annas = login.session_cookie(anna)
    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{anna['id']}",
        json={"phone": "+41791234567"},
        headers={**cookie_headers(managers), "If-Match": "3"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/v1/auth/me", headers=cookie_headers(annas)).status_code == 200, "a phone is not a claim"

    sister = create_dealership(client)
    db_session.add(DealershipMembership(user_id=uuid.UUID(manager["id"]), dealership_id=uuid.UUID(sister), is_dealer_manager=True))
    db_session.commit()
    response = client.post(
        f"/v1/dealerships/{dealership}/users/{anna['id']}/memberships",
        json={"dealershipId": sister, "isDealerManager": False},
        headers={**cookie_headers(managers), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    assert client.get("/v1/auth/me", headers=cookie_headers(annas)).status_code == 401


def test_a_token_without_a_session_row_is_left_to_the_route(client):
    """A token minted without `sid` — the shape every test and a bootstrap
    mints — is not the guard's business: the route's own principal check
    answers it, as before KAN-196."""

    dealership = create_dealership(client)
    tid = uuid.UUID(dealership)
    bare = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid4(), roles={AccessRole.PLATFORM_ADMIN}
    )
    assert client.get(f"/v1/dealerships/{dealership}", headers={"Authorization": f"Bearer {bare}"}).status_code == 200


def test_a_token_naming_an_unknown_session_is_refused(client):
    dealership = create_dealership(client)
    tid = uuid.UUID(dealership)
    ghost = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tid,
        group_id=uuid.uuid4(),
        roles={AccessRole.PLATFORM_ADMIN},
        session_id=uuid.uuid4(),
    )
    response = client.get(f"/v1/dealerships/{dealership}", headers={"Authorization": f"Bearer {ghost}"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == SESSION_ENDED


def test_a_stale_cookie_does_not_block_an_unauthenticated_route(client):
    """The guard judges only tokens it can read: a garbage cookie on the
    login route is the route's business (it ignores it), not a 401."""

    response = client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.ch", "password": "whatever-it-is-12"},
        headers={"Cookie": "dms_session=not-a-token"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] != SESSION_ENDED
