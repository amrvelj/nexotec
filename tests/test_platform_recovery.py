"""KAN-202 — platform staff's account recovery (Authentication & Identity
FR-AU-25…27, D-A-16, D-A-36): five actions at one door, each platform
staff's alone, from a session with a second factor, never under an
impersonation, with a mandatory stated reason, its own auth event in the
Betrieb's stream (reason included — Anto, 2026-10-22), an audit row naming
the staff member and a `security` notice owed to the user.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.base import utcnow
from app.main import app
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.credential import Credential
from app.platform.models.mfa import MfaTotp
from app.platform.models.user import User
from app.platform.services import security_notifications
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    change_email,
    cookie_headers,
    create_dealership,
    create_user,
    deactivate,
    last_mail_to,
    platform_admin_headers,
    recover,
    token_in,
)

REASON = "Asked by the Betrieb's owner on the phone, ticket 4711."
REASON_EMAIL = "The user's address moved to the new domain, ticket 4712."
ACTIONS = ("reissue-invitation", "reset-mfa", "clear-lockout", "revoke-sessions")


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


@pytest.fixture()
def notices():
    sent: list[security_notifications.SecurityNotice] = []
    security_notifications.register_sink(lambda db, notice: sent.append(notice))
    yield sent
    security_notifications.reset_sink()


def _version(client, dealership, user_id) -> int:
    """The user row's version as its Betrieb reads it; 1 for a user the
    Betrieb does not have (the request is refused before If-Match matters)."""

    row = client.get(f"/v1/dealerships/{dealership}/users/{user_id}", headers=platform_admin_headers())
    return row.json()["version"] if row.status_code == 200 else 1


def _grant_manager(client, dealership, user_id, *, headers=None, reason=REASON, if_match=None):
    version = if_match if if_match is not None else _version(client, dealership, user_id)
    return client.post(
        f"/v1/platform/dealerships/{dealership}/recovery/grant-manager",
        json={"userId": str(user_id), "reason": reason},
        headers={**(headers if headers is not None else platform_admin_headers()), "If-Match": str(version)},
    )


def _lock(db_session, user_id) -> None:
    credential = db_session.scalar(select(Credential).where(Credential.user_id == uuid.UUID(str(user_id))))
    credential.failed_login_attempts = 5
    credential.last_failed_at = utcnow()
    credential.locked_until = utcnow() + dt.timedelta(minutes=15)
    db_session.commit()


def _enrol_totp(client, cookie) -> None:
    import pyotp

    begun = client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie)).json()
    confirmed = client.post(
        "/v1/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(begun["secret"]).now()}, headers=cookie_headers(cookie)
    )
    assert confirmed.status_code == 200, confirmed.text


def _recovery_events(db_session) -> list[AuthEvent]:
    db_session.expire_all()
    return [e for e in db_session.scalars(select(AuthEvent).order_by(AuthEvent.occurred_at)).all() if e.kind.value.startswith("recovery_")]


# --- each action -----------------------------------------------------------------------


def test_reissue_invitation_mails_a_fresh_link_and_records_the_reason(client, login, mailbox, db_session, notices):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    staff_id = uuid.uuid4()

    response = recover(client, anna["id"], "reissue-invitation", headers=platform_admin_headers(user_id=staff_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "reissue-invitation"
    assert body["userId"] == anna["id"] and body["dealershipId"] == dealership

    # FR-AU-26: a re-issued invitation is a flow and still goes by e-mail.
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    accepted = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": "a-brand-new-password-2026"})
    assert accepted.status_code == 200, accepted.text

    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_INVITATION_REISSUED
    assert str(event.id) == body["authEventId"]
    assert event.actor_id == staff_id
    assert str(event.dealership_id) == dealership
    assert event.details == {"reason": REASON}
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "recovery_reissue_invitation")).one()
    assert audit.actor_id == staff_id and str(audit.entity_id) == anna["id"] and audit.reason == REASON
    assert [(n.kind, n.actor_id, str(n.user_id)) for n in notices] == [
        ("recovery_invitation_reissued", staff_id, anna["id"])
    ]


def test_reset_mfa_removes_every_factor_and_ends_the_sessions(client, login, db_session, notices):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    _enrol_totp(client, cookie)

    response = recover(client, anna["id"], "reset-mfa")
    assert response.status_code == 200, response.text

    assert db_session.scalars(select(MfaTotp)).all() == []
    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).status_code == 401
    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_MFA_RESET
    assert event.details == {"reason": REASON, "sessionsEnded": 1}
    # The mechanism's own event is recorded beside it, as for any removal.
    assert db_session.scalars(select(AuthEvent).where(AuthEvent.kind == AuthEventKind.MFA_REMOVED)).one()
    assert [n.kind for n in notices] == ["recovery_mfa_reset"]


def test_clear_lockout_lets_the_user_sign_in_again(client, login, db_session, notices):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    _lock(db_session, anna["id"])
    assert login.login(anna).status_code == 401

    response = recover(client, anna["id"], "clear-lockout")
    assert response.status_code == 200, response.text

    assert login.login(anna, STRONG_PASSWORD).status_code == 200
    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_LOCKOUT_CLEARED
    assert event.details == {"reason": REASON, "failedLoginAttempts": 5}
    assert [n.kind for n in notices] == ["recovery_lockout_cleared"]


def test_clear_lockout_on_an_account_that_is_not_locked_is_a_409(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)

    response = recover(client, anna["id"], "clear-lockout")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"rule": "not_locked"}
    # An invited user holds no credential: nothing to clear either.
    beat = create_user(client, dealership, isDealerManager=False)
    assert recover(client, beat["id"], "clear-lockout").status_code == 409
    assert _recovery_events(db_session) == []


def test_revoke_sessions_ends_every_session_of_the_user(client, login, db_session, notices):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    one = login.session_cookie(anna)
    two = login.session_cookie(anna)

    response = recover(client, anna["id"], "revoke-sessions")
    assert response.status_code == 200, response.text

    assert client.get("/v1/auth/me", headers=cookie_headers(one)).status_code == 401
    assert client.get("/v1/auth/me", headers=cookie_headers(two)).status_code == 401
    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_SESSIONS_REVOKED
    assert event.details == {"reason": REASON, "sessionsEnded": 2}
    assert [n.kind for n in notices] == ["recovery_sessions_revoked"]


def test_grant_manager_makes_an_active_user_the_betriebs_manager_at_any_time(client, login, db_session, notices):
    """Anto, 2026-10-22: at any time, with a reason — not only when the last
    manager has left. The Betrieb here still has its manager."""

    dealership = create_dealership(client)
    create_user(client, dealership)  # the manager, still there
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    staff_id = uuid.uuid4()

    response = _grant_manager(client, dealership, beat["id"], headers=platform_admin_headers(user_id=staff_id))
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "grant-manager"

    db_session.expire_all()
    assert db_session.get(User, uuid.UUID(beat["id"])).is_dealer_manager is True
    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_MANAGER_GRANTED
    assert event.actor_id == staff_id and event.details == {"reason": REASON}
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "recovery_grant_manager")).one()
    assert audit.actor_id == staff_id and audit.reason == REASON
    assert [n.kind for n in notices] == ["recovery_manager_granted"]


def test_grant_manager_refuses_what_it_cannot_grant(client, login, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    login.accept_invitation(manager)
    invited = create_user(client, dealership, isDealerManager=False)
    other = create_dealership(client)

    already = _grant_manager(client, dealership, manager["id"])
    assert already.status_code == 409
    assert already.json()["error"]["details"] == {"rule": "already_manager"}
    # "An existing active user": not one who never accepted the invitation.
    not_active = _grant_manager(client, dealership, invited["id"])
    assert not_active.status_code == 409
    assert not_active.json()["error"]["details"]["rule"] == "not_active"
    # A stale version is a 409 like any other write of the user row.
    assert _grant_manager(client, dealership, invited["id"], if_match=99).status_code == 409
    # Another Betrieb's user is not this Betrieb's: 404.
    assert _grant_manager(client, other, manager["id"]).status_code == 404
    assert _grant_manager(client, uuid.uuid4(), manager["id"]).status_code == 404
    assert _recovery_events(db_session) == []


# --- the gate --------------------------------------------------------------------------


def test_only_platform_staff_with_a_second_factor_reach_the_recovery_door(client, login, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    managers = cookie_headers(login.session_cookie(manager))
    password_only = platform_admin_headers(mfa=False)

    for headers, message in ((managers, "platform staff"), (password_only, "second factor")):
        for action in ACTIONS:
            refused = recover(client, beat["id"], action, headers=headers)
            assert refused.status_code == 403, (action, refused.text)
            assert message in refused.json()["error"]["message"]
        refused = _grant_manager(client, dealership, beat["id"], headers=headers, if_match=2)
        assert refused.status_code == 403 and message in refused.json()["error"]["message"]
        refused = change_email(client, beat["id"], "beat.neu@example.ch", if_match=2, headers=headers)
        assert refused.status_code == 403 and message in refused.json()["error"]["message"]
        refused = client.get("/v1/platform/users", params={"email": beat["email"]}, headers=headers)
        assert refused.status_code == 403 and message in refused.json()["error"]["message"]
    assert _recovery_events(db_session) == []


def test_recovery_is_never_available_through_an_impersonation(client, login, db_session):
    """FR-AU-27 / FR-A-17. Impersonation is not built: no token carries it
    yet, so the principal is put in place directly — the gate refuses it
    whatever else it holds (staff, second factor)."""

    dealership = create_dealership(client)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    impersonating = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(dealership),
        group_id=uuid.uuid4(),
        memberships=frozenset({uuid.UUID(dealership)}),
        active_location_id=None,
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        is_dealer_manager=False,
        amr=frozenset({"pwd", "otp", "mfa"}),
        mfa=True,
        impersonator_id=uuid.uuid4(),
    )
    app.dependency_overrides[get_current_principal] = lambda: impersonating
    try:
        for action in ACTIONS:
            refused = client.post(f"/v1/platform/users/{beat['id']}/recovery/{action}", json={"reason": REASON})
            assert refused.status_code == 403, (action, refused.text)
            assert "impersonation" in refused.json()["error"]["message"]
        refused = client.post(
            f"/v1/platform/dealerships/{dealership}/recovery/grant-manager",
            json={"userId": beat["id"], "reason": REASON},
            headers={"If-Match": "2"},
        )
        assert refused.status_code == 403 and "impersonation" in refused.json()["error"]["message"]
        refused = client.post(
            f"/v1/platform/users/{beat['id']}/recovery/change-email",
            json={"email": "beat.neu@example.ch", "reason": REASON},
            headers={"If-Match": "2"},
        )
        assert refused.status_code == 403 and "impersonation" in refused.json()["error"]["message"]
    finally:
        app.dependency_overrides.pop(get_current_principal, None)
    assert _recovery_events(db_session) == []


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}])
def test_a_recovery_action_needs_a_stated_reason(client, login, db_session, body):
    dealership = create_dealership(client)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)

    for action in ACTIONS:
        response = client.post(
            f"/v1/platform/users/{beat['id']}/recovery/{action}", json=body, headers=platform_admin_headers()
        )
        assert response.status_code == 422, (action, response.text)
    grant = client.post(
        f"/v1/platform/dealerships/{dealership}/recovery/grant-manager",
        json={"userId": beat["id"], **body},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    assert grant.status_code == 422, grant.text
    moved = client.post(
        f"/v1/platform/users/{beat['id']}/recovery/change-email",
        json={"email": "beat.neu@example.ch", **body},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    assert moved.status_code == 422, moved.text
    assert _recovery_events(db_session) == []


def test_staff_do_not_recover_their_own_account_nor_a_deactivated_one(client, login, db_session):
    dealership = create_dealership(client)
    create_user(client, dealership)
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)
    login.accept_invitation(staff)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    assert deactivate(client, dealership, beat, if_match=2).status_code == 200

    own = platform_admin_headers(user_id=uuid.UUID(staff["id"]))
    for action in ACTIONS:
        mine = recover(client, staff["id"], action, headers=own)
        assert mine.status_code == 403 and mine.json()["error"]["details"] == {"rule": "self"}
        gone = recover(client, beat["id"], action)
        assert gone.status_code == 409 and gone.json()["error"]["details"]["rule"] == "deactivated"
    assert change_email(client, staff["id"], "nora.neu@example.ch", if_match=2, headers=own).status_code == 403
    assert recover(client, uuid.uuid4(), "reset-mfa").status_code == 404
    assert client.post(f"/v1/platform/users/{beat['id']}/recovery/set-password", json={"reason": REASON}, headers=platform_admin_headers()).status_code == 422
    assert _recovery_events(db_session) == []


# --- the Betrieb sees it ---------------------------------------------------------------


def test_every_recovery_action_is_listed_in_the_betriebs_auth_stream_with_its_reason(client, login, db_session):
    """FR-AU-26 / FR-AU-21: a manager who did not ask for it sees it — the
    action, the staff member and the reason."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = cookie_headers(login.session_cookie(manager))
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    _lock(db_session, beat["id"])
    staff_id = uuid.uuid4()
    staff = platform_admin_headers(user_id=staff_id)

    for action in ("clear-lockout", "reset-mfa", "revoke-sessions", "reissue-invitation"):
        assert recover(client, beat["id"], action, headers=staff, reason=f"{action} for the owner").status_code == 200
    login.accept_invitation(beat, password="a-brand-new-password-2026")
    assert _grant_manager(client, dealership, beat["id"], headers=staff, reason="the owner asked").status_code == 200
    moved = change_email(client, beat["id"], "beat.neu@example.ch", if_match=_version(client, dealership, beat["id"]), headers=staff, reason="new domain")
    assert moved.status_code == 200, moved.text

    stream = client.get(f"/v1/dealerships/{dealership}/auth-events", headers=managers)
    assert stream.status_code == 200, stream.text
    listed = {e["kind"]: e for e in stream.json()["items"] if e["kind"].startswith("recovery_")}
    assert set(listed) == {
        "recovery_lockout_cleared",
        "recovery_mfa_reset",
        "recovery_sessions_revoked",
        "recovery_invitation_reissued",
        "recovery_manager_granted",
        "recovery_email_changed",
    }
    for event in listed.values():
        assert event["actorId"] == str(staff_id)
        assert event["userId"] == beat["id"]
    assert listed["recovery_lockout_cleared"]["details"]["reason"] == "clear-lockout for the owner"
    assert listed["recovery_manager_granted"]["details"]["reason"] == "the owner asked"
    assert listed["recovery_email_changed"]["details"] == {"reason": "new domain", "from": beat["email"], "to": "beat.neu@example.ch"}


# --- finding the account ---------------------------------------------------------------


def test_staff_find_an_account_by_exact_address_or_by_betrieb(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    _lock(db_session, anna["id"])
    beat = create_user(client, dealership, isDealerManager=False)
    other = create_dealership(client)
    create_user(client, other)

    by_email = client.get("/v1/platform/users", params={"email": anna["email"].upper()}, headers=platform_admin_headers())
    assert by_email.status_code == 200, by_email.text
    (row,) = by_email.json()["items"]
    assert row["id"] == anna["id"] and row["dealershipId"] == dealership
    assert row["dealershipName"] == "Garage Musterbetrieb AG"
    assert row["hasPassword"] is True and row["lockedUntil"] is not None and row["failedLoginAttempts"] == 5
    assert row["mfaEnrolled"] is False and row["isDealerManager"] is True and row["isPlatformStaff"] is False
    assert "argon2" not in by_email.text and "passwordHash" not in by_email.text
    # A partial address finds nothing: staff look up, they do not browse.
    partial = client.get("/v1/platform/users", params={"email": anna["email"][:5]}, headers=platform_admin_headers())
    assert partial.json()["items"] == []

    by_betrieb = client.get("/v1/platform/users", params={"dealershipId": dealership}, headers=platform_admin_headers())
    assert {u["id"] for u in by_betrieb.json()["items"]} == {anna["id"], beat["id"]}
    assert client.get("/v1/platform/users", headers=platform_admin_headers()).status_code == 400
    assert (
        client.get("/v1/platform/users", params={"dealershipId": str(uuid.uuid4())}, headers=platform_admin_headers()).status_code
        == 404
    )


def test_change_email_moves_the_sign_in_address_with_its_consequences(client, login, mailbox, db_session, notices):
    """Anto, 2026-10-22: the staff change of an accepted account's address is
    recovery — a reason, its own event, the KAN-196 consequences."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    annas = login.session_cookie(anna)
    staff_id = uuid.uuid4()

    response = change_email(client, anna["id"], "Anna.Neu@example.ch", if_match=2, headers=platform_admin_headers(user_id=staff_id))
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "change-email" and response.json()["userVersion"] == 3

    assert client.get("/v1/auth/me", headers=cookie_headers(annas)).status_code == 401
    assert "anna.neu@example.ch" in last_mail_to(mailbox, anna["email"]).text  # the old address is told
    assert client.post("/v1/auth/login", json={"email": "anna.neu@example.ch", "password": STRONG_PASSWORD}).status_code == 200
    (event,) = _recovery_events(db_session)
    assert event.kind == AuthEventKind.RECOVERY_EMAIL_CHANGED and event.actor_id == staff_id
    assert event.details == {"reason": REASON_EMAIL, "from": anna["email"], "to": "anna.neu@example.ch"}
    assert db_session.scalars(select(AuthEvent).where(AuthEvent.kind == AuthEventKind.EMAIL_CHANGED)).one()
    assert [n.kind for n in notices] == ["recovery_email_changed"]

    same = change_email(client, anna["id"], "anna.neu@example.ch", if_match=3)
    assert same.status_code == 409 and same.json()["error"]["details"] == {"rule": "same_email"}
    assert change_email(client, anna["id"], "anna.wieder@example.ch", if_match=2).status_code == 409  # stale version


def test_the_invitation_re_send_still_wants_a_factor_from_staff(client):
    """The onboarding re-send keeps `require_mfa_session` for a staff caller
    (the reviewer's gap: the moved test no longer covered it)."""

    dealership = create_dealership(client)
    beat = create_user(client, dealership, isDealerManager=False)
    path = f"/v1/dealerships/{dealership}/users/{beat['id']}/invitations"

    refused = client.post(path, headers=platform_admin_headers(mfa=False))
    assert refused.status_code == 403 and "second factor" in refused.json()["error"]["message"]
    assert client.post(path, headers=platform_admin_headers()).status_code == 202


def test_a_search_says_when_it_shows_only_part_of_the_matches(client, monkeypatch):
    from app.platform.services import recovery as recovery_service

    dealership = create_dealership(client)
    create_user(client, dealership)
    create_user(client, dealership, isDealerManager=False)
    monkeypatch.setattr(recovery_service, "SEARCH_LIMIT", 1)

    found = client.get("/v1/platform/users", params={"dealershipId": dealership}, headers=platform_admin_headers())
    assert found.status_code == 200, found.text
    assert len(found.json()["items"]) == 1 and found.json()["truncated"] is True
