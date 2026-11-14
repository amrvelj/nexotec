"""KAN-229 — per-Betrieb session policy (D-A-37, FR-AU-28; Anto 2026-11-11,
option 1): a Betrieb may set idle 15–120 minutes and an absolute maximum of
1–12 hours; the session ends at whichever comes first, login + the hours or
the next midnight Europe/Zurich; platform staff always get the platform
values; a Betrieb that sets nothing gets them too; a switch applies the
target's policy but never makes the session last longer; the manager alone
writes it, audited.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import decode_token_claims
from app.platform.identity.models.auth_session import AuthSession
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.services import auth_sessions, session_policy
from app.platform.identity.services.session_policy import SessionPolicy
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _row(db_session, token: str) -> AuthSession:
    db_session.expire_all()
    row = db_session.get(AuthSession, uuid.UUID(decode_token_claims(token)["sid"]))
    assert row is not None
    return row


def _config(client, dealership: str, headers: dict | None = None):
    response = client.get(f"/v1/dealerships/{dealership}/auth-config", headers=headers or platform_admin_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _put(client, dealership: str, body: dict, *, headers: dict | None = None):
    current = _config(client, dealership)
    payload = {"mfaEnforced": current["mfaEnforced"], **body}
    return client.put(
        f"/v1/dealerships/{dealership}/auth-config",
        json=payload,
        headers={**(headers or platform_admin_headers()), "If-Match": str(current["version"])},
    )


def _set_policy(client, dealership: str, *, idle: int | None, hours: int | None) -> None:
    response = _put(client, dealership, {"sessionIdleMinutes": idle, "sessionAbsoluteHours": hours})
    assert response.status_code == 200, response.text


# --- the API --------------------------------------------------------------------------------


def test_an_unset_policy_reads_as_null_with_the_bounds(client):
    dealership = create_dealership(client)
    body = _config(client, dealership)
    assert body["sessionIdleMinutes"] is None
    assert body["sessionAbsoluteHours"] is None
    assert body["sessionBounds"] == {
        "idleMinutesMin": 15,
        "idleMinutesMax": 120,
        "absoluteHoursMin": 1,
        "absoluteHoursMax": 12,
        "platformIdleMinutes": 120,
    }


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"sessionIdleMinutes": 14}, "session_idle_minutes_out_of_bounds"),
        ({"sessionIdleMinutes": 121}, "session_idle_minutes_out_of_bounds"),
        ({"sessionAbsoluteHours": 0}, "session_absolute_hours_out_of_bounds"),
        ({"sessionAbsoluteHours": 13}, "session_absolute_hours_out_of_bounds"),
    ],
)
def test_a_value_outside_the_bounds_is_422_with_a_stable_reason(client, body, reason):
    dealership = create_dealership(client)
    response = _put(client, dealership, body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason
    assert _config(client, dealership)["sessionIdleMinutes"] is None


@pytest.mark.parametrize(("idle", "hours"), [(15, 1), (120, 12), (45, None), (None, 8)])
def test_the_bounds_themselves_are_accepted(client, idle, hours):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=idle, hours=hours)
    body = _config(client, dealership)
    assert (body["sessionIdleMinutes"], body["sessionAbsoluteHours"]) == (idle, hours)


def test_a_body_with_only_mfa_leaves_the_policy_unchanged_and_null_resets_it(client):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=30, hours=6)

    assert _put(client, dealership, {}).status_code == 200  # mfaEnforced only
    assert (_config(client, dealership)["sessionIdleMinutes"], _config(client, dealership)["sessionAbsoluteHours"]) == (
        30,
        6,
    )

    assert _put(client, dealership, {"sessionIdleMinutes": None}).status_code == 200
    body = _config(client, dealership)
    assert (body["sessionIdleMinutes"], body["sessionAbsoluteHours"]) == (None, 6)


def test_only_the_manager_writes_the_policy_and_the_write_is_audited(client, login, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    seller = create_user(client, dealership, isDealerManager=False)
    seller_headers = cookie_headers(login.session_cookie(seller))
    manager_headers = cookie_headers(login.session_cookie(manager))

    # Any role reads.
    assert _config(client, dealership, seller_headers)["sessionIdleMinutes"] is None
    refused = _put(client, dealership, {"sessionIdleMinutes": 30}, headers=seller_headers)
    assert refused.status_code == 403, refused.text

    accepted = _put(client, dealership, {"sessionIdleMinutes": 30, "sessionAbsoluteHours": 4}, headers=manager_headers)
    assert accepted.status_code == 200, accepted.text

    db_session.expire_all()
    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == uuid.UUID(dealership),
            AuditEvent.action == "auth_config_set",
            AuditEvent.actor_id == uuid.UUID(manager["id"]),
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].before == {"session_idle_minutes": None, "session_absolute_hours": None}
    assert audit[0].after == {"session_idle_minutes": 30, "session_absolute_hours": 4}


# --- sessions minted under the policy -------------------------------------------------------


def test_a_login_in_a_betrieb_without_a_policy_gets_the_platform_values(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    row = _row(db_session, login.session_cookie(anna))
    assert row.idle_minutes == 120
    # Capped at the absolute end, as the next test says: true after 22:00 Zurich time too.
    assert row.idle_expires_at == min(row.created_at + dt.timedelta(minutes=120), row.absolute_expires_at)
    assert row.absolute_expires_at == auth_sessions.absolute_expiry(row.created_at)


def test_a_login_runs_under_the_betriebs_idle_and_absolute_limits(client, login, db_session):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=20, hours=1)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    row = _row(db_session, token)

    midnight = auth_sessions.absolute_expiry(row.created_at)
    assert row.idle_minutes == 20
    assert row.absolute_expires_at == min(midnight, row.created_at + dt.timedelta(hours=1))
    assert row.idle_expires_at == min(row.created_at + dt.timedelta(minutes=20), row.absolute_expires_at)
    # The token and the cookie end with the session.
    assert decode_token_claims(token)["exp"] == int(row.absolute_expires_at.timestamp())


def test_activity_extends_the_idle_end_by_the_betriebs_window_not_the_platforms(client, login, db_session):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=20, hours=None)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    row = _row(db_session, token)
    now = dt.datetime.now(dt.UTC)
    row.last_seen_at = now - dt.timedelta(minutes=5)
    db_session.commit()

    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 200
    row = _row(db_session, token)
    assert row.idle_expires_at <= dt.datetime.now(dt.UTC) + dt.timedelta(minutes=20)
    assert row.idle_expires_at >= min(now + dt.timedelta(minutes=19), row.absolute_expires_at)


def test_a_session_past_the_betriebs_idle_window_is_refused(client, login, db_session):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=15, hours=None)
    anna = create_user(client, dealership)
    token = login.session_cookie(anna)
    row = _row(db_session, token)
    # Sixteen minutes without a request.
    row.last_seen_at = row.last_seen_at - dt.timedelta(minutes=16)
    row.idle_expires_at = row.last_seen_at + dt.timedelta(minutes=15)
    db_session.commit()

    assert client.get("/v1/auth/me", headers=cookie_headers(token)).status_code == 401


def test_platform_staff_always_get_the_platform_values(db_session, client):
    dealership = create_dealership(client)
    _set_policy(client, dealership, idle=15, hours=1)
    staff = session_policy.effective_policy(db_session, dealership_id=uuid.UUID(dealership), is_platform_staff=True)
    assert staff == session_policy.platform_policy()
    member = session_policy.effective_policy(db_session, dealership_id=uuid.UUID(dealership), is_platform_staff=False)
    assert member == SessionPolicy(idle_minutes=15, absolute_hours=1)


# --- the absolute end: whichever comes first ------------------------------------------------


@pytest.mark.parametrize(
    ("started_at", "hours", "expected"),
    [
        # 10:00 Zurich (CEST) + 4 h = 14:00, before midnight.
        (dt.datetime(2026, 10, 17, 8, 0, tzinfo=dt.UTC), 4, dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)),
        # 21:00 Zurich + 12 h would be 09:00 — midnight comes first.
        (dt.datetime(2026, 10, 17, 19, 0, tzinfo=dt.UTC), 12, dt.datetime(2026, 10, 17, 22, 0, tzinfo=dt.UTC)),
        # No hours of the Betrieb's own: midnight.
        (dt.datetime(2026, 10, 17, 8, 0, tzinfo=dt.UTC), None, dt.datetime(2026, 10, 17, 22, 0, tzinfo=dt.UTC)),
    ],
)
def test_the_absolute_end_is_whichever_comes_first(started_at, hours, expected):
    policy = SessionPolicy(idle_minutes=60, absolute_hours=hours)
    midnight = auth_sessions.absolute_expiry(started_at)
    assert session_policy.absolute_end(started_at=started_at, policy=policy, next_midnight=midnight) == expected


# --- switching Betrieb ---------------------------------------------------------------------


def _member_of_two(client, db_session, login):
    home = create_dealership(client)
    sister = create_dealership(client)
    anna = create_user(client, home)
    db_session.add(DealershipMembership(user_id=uuid.UUID(anna["id"]), dealership_id=uuid.UUID(sister)))
    db_session.commit()
    return home, sister, anna


def test_switching_to_a_stricter_betrieb_shortens_the_session(client, login, db_session):
    _home, sister, anna = _member_of_two(client, db_session, login)
    _set_policy(client, sister, idle=15, hours=1)
    token = login.session_cookie(anna)
    before = _row(db_session, token)
    created_at, absolute_before = before.created_at, before.absolute_expires_at

    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": sister}, headers=cookie_headers(token))
    assert response.status_code == 200, response.text
    switched = response.cookies.get("dms_session")

    row = _row(db_session, switched)
    assert row.idle_minutes == 15
    assert row.absolute_expires_at == min(absolute_before, created_at + dt.timedelta(hours=1))
    assert row.idle_expires_at <= dt.datetime.now(dt.UTC) + dt.timedelta(minutes=15)
    assert decode_token_claims(switched)["exp"] == int(row.absolute_expires_at.timestamp())


def test_switching_to_a_looser_betrieb_never_lengthens_the_session(client, login, db_session):
    home, sister, anna = _member_of_two(client, db_session, login)
    _set_policy(client, home, idle=15, hours=1)
    token = login.session_cookie(anna)
    before = _row(db_session, token)
    absolute_before, idle_before = before.absolute_expires_at, before.idle_expires_at

    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": sister}, headers=cookie_headers(token))
    assert response.status_code == 200, response.text

    row = _row(db_session, response.cookies.get("dms_session"))
    assert row.absolute_expires_at == absolute_before
    assert row.idle_expires_at <= idle_before
    # The target's (platform) window applies to later activity.
    assert row.idle_minutes == 120


def test_a_refused_switch_leaves_the_session_as_it_was(client, login, db_session):
    home = create_dealership(client)
    stranger = create_dealership(client)
    _set_policy(client, stranger, idle=15, hours=1)
    anna = create_user(client, home)
    token = login.session_cookie(anna)
    before = _row(db_session, token)
    absolute_before = before.absolute_expires_at

    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": stranger}, headers=cookie_headers(token))
    assert response.status_code == 403
    assert _row(db_session, token).absolute_expires_at == absolute_before
