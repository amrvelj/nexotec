"""KAN-196 — second factors (D-A-09, D-A-36, A-13, FR-AU-11/12): TOTP
enrolment and the login step, replay refused, recovery codes once each,
passkey options bound to a single-use challenge, platform staff refused on a
gated route without a factor in the session, the Betrieb's enforcement
switch, a manager's removal naming the actor, the auth-event stream.
"""

import base64
import datetime as dt
import hashlib
import json
import os
import uuid

import pyotp
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, Principal, create_access_token, decode_token_claims, require_mfa_session
from app.core.errors import register_error_handlers
from app.db import get_db
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.mfa import MfaPasskey, MfaRecoveryCode, MfaTotp
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _events(db_session, kind: AuthEventKind) -> list[AuthEvent]:
    return list(db_session.scalars(select(AuthEvent).where(AuthEvent.kind == kind)).all())


def _enrol_totp(client, cookie: str) -> tuple[str, list[str]]:
    begun = client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie))
    assert begun.status_code == 200, begun.text
    secret = begun.json()["secret"]
    assert begun.json()["provisioningUri"].startswith("otpauth://totp/")
    confirmed = client.post(
        "/v1/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=cookie_headers(cookie)
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["codes"]


def _wait_out_backoff(db_session, user: dict) -> None:
    """Moves the account's last failure an hour back, as a patient guesser
    would wait — so a test about another limit is not stopped by this one."""

    from app.platform.models.credential import Credential

    row = db_session.scalar(select(Credential).where(Credential.user_id == user["id"]))
    db_session.refresh(row)
    if row.last_failed_at is not None:
        row.last_failed_at = row.last_failed_at - dt.timedelta(hours=1)
        db_session.commit()


def _fresh_code(db_session, secret: str) -> str:
    """The current code, with the replay guard rewound as if a step had
    passed since the last use — the tests cannot wait thirty seconds."""

    totp = db_session.scalars(select(MfaTotp)).one()
    totp.last_used_step = None
    db_session.commit()
    return pyotp.TOTP(secret).now()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- TOTP ---------------------------------------------------------------------------------


def test_totp_enrolment_confirms_with_a_valid_code_and_hands_out_ten_recovery_codes(client, login, db_session):
    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    before = client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).json()
    assert before == {
        "totpEnrolled": False,
        "passkeys": [],
        "recoveryCodesRemaining": 0,
        "enforced": True,
        "enrolmentRequired": True,
    }

    begun = client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie))
    secret = begun.json()["secret"]
    wrong = client.post("/v1/auth/mfa/totp/confirm", json={"code": "000000"}, headers=cookie_headers(cookie))
    assert wrong.status_code == 422 and wrong.json()["error"]["details"] == {"factor": "totp"}, wrong.text
    confirmed = client.post(
        "/v1/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=cookie_headers(cookie)
    )
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(code) == 11 and code[5] == "-" for code in codes)

    after = client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).json()
    assert after["totpEnrolled"] is True
    assert after["recoveryCodesRemaining"] == 10
    assert after["enrolmentRequired"] is False
    assert len(_events(db_session, AuthEventKind.MFA_ENROLLED)) == 1
    # The secret is stored encrypted; the recovery codes as hashes only.
    totp = db_session.scalars(select(MfaTotp)).one()
    assert totp.confirmed_at is not None
    stored = {row.code_hash for row in db_session.scalars(select(MfaRecoveryCode)).all()}
    assert not (stored & set(codes))
    # A second enrolment needs the first removed.
    assert client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie)).status_code == 409


def test_a_login_with_a_factor_needs_the_second_step(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    secret, _ = _enrol_totp(client, login.session_cookie(anna))

    first = login.login(anna)
    assert first.status_code == 200, first.text
    assert first.json()["mfaRequired"] is True
    assert first.json()["session"] is None
    assert first.json()["factors"] == ["totp", "recovery_code"]
    assert "dms_session=" not in first.headers.get("set-cookie", "")
    challenge = first.json()["challengeId"]

    wrong = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": "000000"})
    assert wrong.status_code == 401
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) == 1

    second = client.post(
        "/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)}
    )
    assert second.status_code == 200, second.text
    assert second.json()["mfaRequired"] is False
    token = second.cookies.get("dms_session")
    claims = decode_token_claims(token)
    assert claims["amr"] == ["mfa", "otp", "pwd"]
    assert claims["mfa"] is True
    assert client.get("/v1/auth/me", headers=cookie_headers(token)).json()["mfaEnrolmentRequired"] is False
    # The challenge is spent.
    spent = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)})
    assert spent.status_code == 401
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": "made-up", "code": "123456"}).status_code == 401


def test_a_totp_code_is_good_once(client, login, db_session):
    """RFC 6238: the code that confirmed the enrolment, or signed in, is
    spent for its step — the same digits are refused a second time."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    secret, _ = _enrol_totp(client, login.session_cookie(anna))
    code = _fresh_code(db_session, secret)

    first = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": first, "code": code}).status_code == 200
    second = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": second, "code": code}).status_code == 401


def test_a_recovery_code_signs_in_once_and_the_set_can_be_regenerated(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    _, codes = _enrol_totp(client, cookie)

    challenge = login.login(anna).json()["challengeId"]
    used = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": codes[0].upper()})
    assert used.status_code == 200, used.text
    assert decode_token_claims(used.cookies.get("dms_session"))["amr"] == ["mfa", "pwd"]
    assert _events(db_session, AuthEventKind.RECOVERY_CODE_USED)[0].details == {"remaining": 9}

    challenge = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": codes[0]}).status_code == 401

    regenerated = client.post("/v1/auth/mfa/recovery-codes/regenerate", headers=cookie_headers(cookie))
    assert regenerated.status_code == 200, regenerated.text
    fresh = regenerated.json()["codes"]
    assert len(fresh) == 10 and not (set(fresh) & set(codes))
    challenge = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": codes[1]}).status_code == 401
    challenge = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": fresh[0]}).status_code == 200


def test_removing_the_authenticator_drops_the_recovery_codes_and_the_second_step(client, login, db_session):
    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    _enrol_totp(client, cookie)

    assert client.delete("/v1/auth/mfa/totp", headers=cookie_headers(cookie)).status_code == 204
    status = client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).json()
    assert status["totpEnrolled"] is False
    assert status["recoveryCodesRemaining"] == 0
    assert status["enrolmentRequired"] is True
    removed = _events(db_session, AuthEventKind.MFA_REMOVED)
    assert [e.details for e in removed] == [{"factor": "totp", "by_self": True}]
    assert login.login(anna).json()["mfaRequired"] is False


def test_five_wrong_answers_end_the_challenge(client, login, db_session, monkeypatch):
    """FR-AU-08 on the second step: a challenge takes five answers, then
    ends. (The account lockout, which the same five answers would also
    trigger, is lifted here so the challenge's own limit is what shows.)"""

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_lockout_failures", 50)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    secret, _ = _enrol_totp(client, login.session_cookie(anna))
    challenge = login.login(anna).json()["challengeId"]
    for i in range(5):
        # Each guess from another address, so the test shows the challenge's
        # own counter at work, not any per-address rule; the account backoff
        # (from the third failure) is waited out below.
        _wait_out_backoff(db_session, anna)
        wrong = client.post(
            "/v1/auth/mfa/verify",
            json={"challengeId": challenge, "code": "000000"},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
        )
        assert wrong.status_code == 401

    _wait_out_backoff(db_session, anna)
    right = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)})
    assert right.status_code == 401
    assert "start again" in right.json()["error"]["message"]
    assert len(_events(db_session, AuthEventKind.LOGIN_THROTTLED)) == 1
    # A fresh challenge works once the backoff has passed.
    _wait_out_backoff(db_session, anna)
    fresh = login.login(anna).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": fresh, "code": _fresh_code(db_session, secret)}).status_code == 200


def test_a_challenge_spent_by_a_twin_request_mints_nothing(client, login, db_session):
    from app.platform.models.auth_token import AuthTokenKind
    from app.platform.services import auth_tokens

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    secret, _ = _enrol_totp(client, login.session_cookie(anna))
    challenge = login.login(anna).json()["challengeId"]
    assert auth_tokens.consume(db_session, raw=challenge, kind=AuthTokenKind.MFA_CHALLENGE) is not None
    db_session.commit()

    response = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)})
    assert response.status_code == 401
    assert "dms_session=" not in response.headers.get("set-cookie", "")


def test_a_reissued_invitation_to_an_enrolled_user_still_needs_the_second_factor(client, login, mailbox, db_session):
    """Auth & Identity risk 4: no new endpoint forgets the factor. Accepting
    the invitation sets the password, ends the old sessions and hands over
    to the MFA step like a login."""

    from tests.identity_support import last_mail_to, token_in

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    secret, _ = _enrol_totp(client, cookie)
    assert client.post(f"/v1/dealerships/{dealership}/users/{anna['id']}/invitations", headers=platform_admin_headers()).status_code == 202
    raw = token_in(last_mail_to(mailbox, anna["email"]))

    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": "a-brand-new-password-2026"})
    assert response.status_code == 200, response.text
    assert response.json()["mfaRequired"] is True
    assert response.json()["session"] is None
    assert "dms_session=" not in response.headers.get("set-cookie", "")
    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).status_code == 401, "old sessions ended"
    verified = client.post(
        "/v1/auth/mfa/verify", json={"challengeId": response.json()["challengeId"], "code": _fresh_code(db_session, secret)}
    )
    assert verified.status_code == 200, verified.text
    assert decode_token_claims(verified.cookies.get("dms_session"))["mfa"] is True


def test_a_passkey_registration_challenge_is_not_a_login_challenge(client, login):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    registration = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie)).json()["challengeId"]

    assert client.post("/v1/auth/mfa/verify", json={"challengeId": registration, "code": "123456"}).status_code == 401
    assert client.post("/v1/auth/mfa/passkey/options", json={"challengeId": registration}).status_code == 401


# --- enforcement ------------------------------------------------------------------------


def test_the_betrieb_decides_enforcement_and_platform_staff_are_always_enforced(client, login, db_session):
    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    assert login.login(anna).json()["session"]["mfaEnrolmentRequired"] is True

    config = client.get(f"/v1/dealerships/{dealership}/auth-config", headers=cookie_headers(cookie))
    assert config.status_code == 200, config.text
    assert config.json() == {"authMethod": "local", "mfaEnforced": True, "version": 1}
    stale = client.put(
        f"/v1/dealerships/{dealership}/auth-config",
        json={"mfaEnforced": False},
        headers={**cookie_headers(cookie), "If-Match": "7"},
    )
    assert stale.status_code == 409
    # A password-only manager session is confined to enrolment — and to this switch.
    assert client.get(f"/v1/dealerships/{dealership}", headers=cookie_headers(cookie)).status_code == 403
    updated = client.put(
        f"/v1/dealerships/{dealership}/auth-config",
        json={"mfaEnforced": False},
        headers={**cookie_headers(cookie), "If-Match": "1"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {"authMethod": "local", "mfaEnforced": False, "version": 2}
    assert client.get(f"/v1/dealerships/{dealership}", headers=cookie_headers(cookie)).status_code == 200
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "auth_config_set")).one()
    assert (audit.before, audit.after) == ({"mfa_enforced": True}, {"mfa_enforced": False})
    assert str(audit.actor_id) == anna["id"]

    assert login.login(anna).json()["session"]["mfaEnrolmentRequired"] is False
    assert client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).json()["enforced"] is False

    # Any user of the Betrieb may read it (the `dealership_settings` row);
    # only the manager writes it; another tenant gets 404.
    beat = create_user(client, dealership, isDealerManager=False)
    beats = login.session_cookie(beat)
    assert client.get(f"/v1/dealerships/{dealership}/auth-config", headers=cookie_headers(beats)).status_code == 200
    assert (
        client.put(
            f"/v1/dealerships/{dealership}/auth-config",
            json={"mfaEnforced": True},
            headers={**cookie_headers(beats), "If-Match": "2"},
        ).status_code
        == 403
    )
    other = create_dealership(client)
    assert client.get(f"/v1/dealerships/{other}/auth-config", headers=cookie_headers(cookie)).status_code == 404

    # D-A-36: staff are enforced whatever the Betrieb says.
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)
    assert login.login(staff).json()["session"]["mfaEnrolmentRequired"] is True
    assert client.get("/v1/auth/mfa", headers=cookie_headers(login.session_cookie(staff))).json()["enforced"] is True


@pytest.fixture()
def probe_client(engine):
    """A route behind `require_mfa_session` (the impersonation and recovery
    endpoints will carry it; none exists yet)."""

    probe = FastAPI()
    register_error_handlers(probe)

    @probe.get("/probe/staff-only")
    def staff_only(principal: Principal = Depends(require_mfa_session())):
        return {"mfa": principal.mfa}

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    probe.dependency_overrides[get_db] = _override_get_db
    return TestClient(probe)


def test_platform_staff_reach_a_gated_route_only_from_a_session_with_a_second_factor(probe_client):
    def bearer(role: AccessRole, *, mfa: bool) -> dict[str, str]:
        tid = uuid.uuid4()
        token = create_access_token(
            user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid4(), roles={role}, amr=("pwd",), mfa=mfa
        )
        return {"Authorization": f"Bearer {token}"}

    refused = probe_client.get("/probe/staff-only", headers=bearer(AccessRole.PLATFORM_ADMIN, mfa=False))
    assert refused.status_code == 403, refused.text
    assert "second factor" in refused.json()["error"]["message"]
    assert probe_client.get("/probe/staff-only", headers=bearer(AccessRole.PLATFORM_ADMIN, mfa=True)).status_code == 200
    # A dealer user's MFA is the Betrieb's policy, not this gate's.
    assert probe_client.get("/probe/staff-only", headers=bearer(AccessRole.SALES, mfa=False)).status_code == 200


def test_platform_staff_reach_the_recovery_endpoints_only_with_a_second_factor_in_the_session(client, login):
    """D-A-36: the recovery endpoints refuse a staff session that was not
    minted with a second factor; a dealer manager's MFA is the Betrieb's
    policy, and a manager never touches a staff account (KAN-97)."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    base = f"/v1/dealerships/{dealership}/users/{anna['id']}"
    password_only = platform_admin_headers(mfa=False)
    with_factor = platform_admin_headers(mfa=True)

    for method, path in (("post", "/invitations"), ("get", "/login-status"), ("delete", "/sessions"), ("delete", "/mfa")):
        refused = getattr(client, method)(base + path, headers=password_only)
        assert refused.status_code == 403, (path, refused.text)
        assert "second factor" in refused.json()["error"]["message"]
        allowed = getattr(client, method)(base + path, headers=with_factor)
        assert allowed.status_code in (200, 202, 204), (path, allowed.text)

    managers = login.session_cookie(manager)
    assert client.get(base + "/login-status", headers=cookie_headers(managers)).status_code == 200
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)
    assert client.get(f"/v1/dealerships/{dealership}/users/{staff['id']}/login-status", headers=cookie_headers(managers)).status_code == 403
    assert client.post(f"/v1/dealerships/{dealership}/users/{staff['id']}/invitations", headers=cookie_headers(managers)).status_code == 403


def test_platform_staff_remove_a_users_factors_and_the_event_names_the_actor(client, login, db_session):
    """D-A-16: resetting a second factor is account recovery — platform
    staff's alone, never a Betrieb manager's (the takeover chain a manager
    could otherwise run: change the address, drop the factor, re-invite)."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    _enrol_totp(client, login.session_cookie(anna))
    assert login.login(anna).json()["mfaRequired"] is True
    staff_id = uuid.uuid4()
    path = f"/v1/dealerships/{dealership}/users/{anna['id']}/mfa"

    assert client.delete(path, headers=cookie_headers(login.session_cookie(manager))).status_code == 403
    response = client.delete(path, headers=platform_admin_headers(user_id=staff_id))
    assert response.status_code == 204, response.text

    removed = _events(db_session, AuthEventKind.MFA_REMOVED)
    assert len(removed) == 1
    assert removed[0].actor_id == staff_id
    assert removed[0].details == {"factors": ["totp"], "by_self": False, "all": True}
    assert db_session.scalars(select(MfaTotp)).all() == []
    assert db_session.scalars(select(MfaRecoveryCode)).all() == []
    assert login.login(anna).json()["mfaRequired"] is False


def test_an_account_cannot_be_sent_back_to_invited(client, login):
    """D-A-16 cannot be bypassed by flipping an active colleague back to
    `invited` and re-inviting them: `invited` is where an account starts."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = login.session_cookie(manager)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)

    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{beat['id']}",
        json={"status": "invited"},
        headers={**cookie_headers(managers), "If-Match": "2"},
    )
    assert response.status_code == 409, response.text
    assert client.patch(
        f"/v1/dealerships/{dealership}/users/{beat['id']}",
        json={"status": "invited"},
        headers={**platform_admin_headers(), "If-Match": "2"},
    ).status_code == 409


def test_a_manager_re_sends_an_invitation_only_while_the_user_never_accepted_it(client, login, mailbox):
    """D-A-16 drawn where onboarding ends: re-sending an invitation to a user
    who never accepted theirs is the manager's; a fresh invitation to an
    active user is account recovery, platform staff's alone."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = login.session_cookie(manager)
    beat = create_user(client, dealership, isDealerManager=False)
    base = f"/v1/dealerships/{dealership}/users/{beat['id']}"

    assert client.post(base + "/invitations", headers=cookie_headers(managers)).status_code == 202
    login.accept_invitation(beat)
    assert client.post(base + "/invitations", headers=cookie_headers(managers)).status_code == 403
    assert client.post(base + "/invitations", headers=platform_admin_headers()).status_code == 202


def test_the_expired_step_is_told_apart_from_a_wrong_code(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    _enrol_totp(client, login.session_cookie(anna))
    wrong = client.post("/v1/auth/mfa/verify", json={"challengeId": "made-up", "code": "123456"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["details"] == {"step": "expired"}
    challenge = login.login(anna).json()["challengeId"]
    typo = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": "000000"})
    assert typo.status_code == 401
    assert typo.json()["error"]["details"] is None


def test_every_wrong_second_step_answer_counts_on_the_account(client, login, db_session):
    """Across challenges: a password holder cannot buy fresh guesses per
    login — every wrong answer at the MFA step is a failure on the account
    (the password step does not clear them), the backoff applies from the
    third and the fifth locks the account, whether the challenges were
    exhausted or abandoned (the fourth review's seventy-five-guess probe)."""

    from app.platform.models.credential import Credential

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    secret, _ = _enrol_totp(client, login.session_cookie(anna))

    def credential():
        row = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
        db_session.refresh(row)
        return row

    def rewind():
        row = credential()
        row.last_failed_at = row.last_failed_at - dt.timedelta(hours=1)
        db_session.commit()

    # Two guesses on one challenge, abandoned; two on the next — each wrong
    # answer waited out patiently: four failures on the account.
    for _ in range(2):
        challenge = login.login(anna, STRONG_PASSWORD).json()["challengeId"]
        for _ in range(2):
            assert client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": "000000"}).status_code == 401
            rewind()
    assert credential().failed_login_attempts == 4
    # The backoff holds at the MFA step too: a right code inside it is refused
    # and the challenge ends.
    challenge = login.login(anna, STRONG_PASSWORD).json()["challengeId"]
    row = credential()
    row.last_failed_at = dt.datetime.now(dt.UTC)
    db_session.commit()
    inside = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)})
    assert inside.status_code == 401
    assert inside.json()["error"]["details"] == {"step": "expired"}
    rewind()
    # The fifth wrong answer locks the account: the password step refuses.
    challenge = login.login(anna, STRONG_PASSWORD).json()["challengeId"]
    assert client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": "000000"}).status_code == 401
    assert credential().locked_until is not None
    assert [e.details.get("step") for e in _events(db_session, AuthEventKind.LOCKED_OUT)] == ["mfa"]
    assert login.login(anna, STRONG_PASSWORD).status_code == 401


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-level UPDATE race: Postgres only")
def test_parallel_wrong_answers_never_exceed_the_attempt_limit(client, login, db_session):
    """The attempt counter is a guarded UPDATE on the challenge row: thirty
    guesses at once see thirty different counts, and at most five are ever
    checked against the secret."""

    from concurrent.futures import ThreadPoolExecutor

    from app.platform.api.auth import MFA_MAX_ATTEMPTS
    from app.platform.models.credential import Credential

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    _enrol_totp(client, login.session_cookie(anna))
    challenge = login.login(anna).json()["challengeId"]

    def guess(i: int) -> int:
        return client.post(
            "/v1/auth/mfa/verify",
            json={"challengeId": challenge, "code": f"{i:06d}"},
            headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
        ).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(guess, range(30)))
    assert set(statuses) == {401}
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) <= MFA_MAX_ATTEMPTS
    assert len(_events(db_session, AuthEventKind.LOGIN_THROTTLED)) >= 1
    # Every answer that reached the secret is a failure on the account axis.
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert credential.failed_login_attempts == len(_events(db_session, AuthEventKind.MFA_FAILED))


def test_removing_the_last_factor_makes_the_session_owe_enrolment_again(client, login, db_session):
    """The guard and /auth/me judge the same row: a session that removes its
    user's last factor proved nothing any more — and the user's other
    sessions, which proved it at sign-in, end (recorded)."""

    from app.platform.models.mfa import MfaTotp

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    secret, _ = _enrol_totp(client, cookie)
    assert client.get("/v1/customers", headers=cookie_headers(cookie)).status_code == 200
    # A sibling that proved the factor at sign-in.
    challenge = login.login(anna).json()["challengeId"]
    sibling = client.post("/v1/auth/mfa/verify", json={"challengeId": challenge, "code": _fresh_code(db_session, secret)})
    assert sibling.status_code == 200, sibling.text
    sibling_cookie = sibling.cookies.get("dms_session")
    assert client.get("/v1/customers", headers=cookie_headers(sibling_cookie)).status_code == 200

    assert client.delete("/v1/auth/mfa/totp", headers=cookie_headers(cookie)).status_code == 204
    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).json()["mfaEnrolmentRequired"] is True
    assert client.get("/v1/customers", headers=cookie_headers(cookie)).status_code == 403
    assert client.get("/v1/auth/me", headers=cookie_headers(sibling_cookie)).status_code == 401
    ended = [e for e in _events(db_session, AuthEventKind.SESSION_REVOKED) if e.details["reason"] == "mfa_removed"]
    assert len(ended) == 1 and ended[0].details["count"] == 1
    assert db_session.scalars(select(MfaTotp)).all() == []


def test_a_session_that_never_proved_a_factor_ends_once_the_user_holds_one(client, login, db_session):
    """Enrolled on device B, device A's password-only session is not left
    confined in limbo (nor able to mint recovery codes): it ends, and the
    SPA goes to sign-in on the 401."""

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    device_a = login.session_cookie(anna)
    device_b = login.session_cookie(anna)
    _enrol_totp(client, device_b)

    assert client.get("/v1/auth/me", headers=cookie_headers(device_b)).json()["mfaEnrolmentRequired"] is False
    ended = client.post("/v1/auth/mfa/recovery-codes/regenerate", headers=cookie_headers(device_a))
    assert ended.status_code == 401
    assert ended.json()["error"]["message"] == "Your session has ended — sign in again."
    assert client.get("/v1/auth/me", headers=cookie_headers(device_a)).status_code == 401
    revoked = [e for e in _events(db_session, AuthEventKind.SESSION_REVOKED) if e.details["reason"] == "factor_enrolled"]
    assert len(revoked) == 1 and str(revoked[0].user_id) == anna["id"]



def test_removing_a_factor_the_user_never_held_touches_no_other_session(client, login, db_session):
    """DELETE /auth/mfa/totp with nothing confirmed removes no factor — so it
    must neither sign the user's other devices out nor write a removal that
    never happened (the sixth review found the sign-out running
    unconditionally, in a Betrieb that does not even enforce a factor)."""

    from app.platform.models.mfa import MfaTotp

    dealership = create_dealership(client, mfa_enforced=False)
    anna = create_user(client, dealership)
    laptop = login.session_cookie(anna)
    phone = login.session_cookie(anna)

    # Nothing enrolled at all.
    assert client.delete("/v1/auth/mfa/totp", headers=cookie_headers(laptop)).status_code == 204
    assert client.get("/v1/auth/me", headers=cookie_headers(phone)).status_code == 200
    # An enrolment begun and abandoned was never a factor either.
    assert client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(laptop)).status_code == 200
    assert client.delete("/v1/auth/mfa/totp", headers=cookie_headers(laptop)).status_code == 204
    assert client.get("/v1/auth/me", headers=cookie_headers(phone)).status_code == 200
    assert client.get("/v1/auth/me", headers=cookie_headers(laptop)).status_code == 200
    assert db_session.scalars(select(MfaTotp)).all() == []
    assert _events(db_session, AuthEventKind.MFA_REMOVED) == []
    assert [e for e in _events(db_session, AuthEventKind.SESSION_REVOKED) if e.details["reason"] == "mfa_removed"] == []


def test_the_guard_re_reads_the_row_before_ending_the_session_that_is_confirming_its_factor(
    client, login, db_session, monkeypatch
):
    """The race the fifth review named: the guard loaded the row (no factor
    proved) while the same session's TOTP confirm committed — the factor now
    exists and the stale row says this session never proved it. The commit is
    injected at exactly that point; without the re-read the enrolling session
    is revoked as `factor_enrolled`, with it the request goes through."""

    from app.core.base import utcnow
    from app.platform.models.auth_session import AuthSession
    from app.platform.models.mfa import MfaTotp
    from app.platform.services import mfa as mfa_service

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    assert client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie)).status_code == 200
    user_id = uuid.UUID(anna["id"])
    real_has_factor = mfa_service.has_factor
    fired = []

    def confirm_commits_now(db, uid):
        # Runs inside the guard, after it loaded the row and before it judges
        # the factor: another connection commits what the confirm would.
        if uid == user_id and not fired:
            fired.append(True)
            totp = db_session.scalars(select(MfaTotp).where(MfaTotp.user_id == user_id)).one()
            totp.confirmed_at = utcnow()
            row = db_session.scalars(
                select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            ).one()
            row.amr = sorted(set(row.amr) | {"otp", "mfa"})
            db_session.commit()
        return real_has_factor(db, uid)

    monkeypatch.setattr(mfa_service, "has_factor", confirm_commits_now)
    response = client.get("/v1/customers", headers=cookie_headers(cookie))
    assert fired, "the guard never asked for the factor"
    assert response.status_code == 200, response.text
    assert [e for e in _events(db_session, AuthEventKind.SESSION_REVOKED) if e.details["reason"] == "factor_enrolled"] == []
    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).json()["mfaEnrolmentRequired"] is False

# --- passkeys ------------------------------------------------------------------------------


def test_passkey_registration_options_are_bound_to_a_challenge_only_success_spends(client, login):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)

    response = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie))
    assert response.status_code == 200, response.text
    challenge_id = response.json()["challengeId"]
    options = json.loads(response.json()["options"])
    assert options["rp"] == {"id": "localhost", "name": "Nexotec DMS"}
    assert options["user"]["name"] == anna["email"]
    assert options["authenticatorSelection"]["residentKey"] == "required"
    expected = hashlib.sha256(f"{challenge_id}:register".encode()).digest()
    assert options["challenge"] == _b64url(expected)

    bogus = {
        "id": "x",
        "rawId": "eA",
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "attestationObject": "oA"},
    }
    refused = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": challenge_id, "credential": bogus, "label": "YubiKey"},
        headers=cookie_headers(cookie),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"factor": "passkey"}
    # A failed attempt leaves the challenge usable for the five minutes it
    # lives (the browser dialog may be cancelled and retried); only a
    # successful registration spends it. A made-up challenge is refused.
    again = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": challenge_id, "credential": bogus, "label": "YubiKey"},
        headers=cookie_headers(cookie),
    )
    assert again.status_code == 422
    made_up = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": "made-up", "credential": bogus, "label": "YubiKey"},
        headers=cookie_headers(cookie),
    )
    assert made_up.status_code == 401


def test_a_passkey_holder_gets_the_passkey_step_and_a_bad_assertion_is_refused(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    credential_id = _b64url(b"a-registered-credential")
    db_session.add(
        MfaPasskey(
            user_id=uuid.UUID(anna["id"]),
            credential_id=credential_id,
            public_key=_b64url(b"not-a-real-cose-key"),
            label="Laptop",
        )
    )
    db_session.commit()
    # The password-only session never proved the passkey and has ended (the
    # guard). A session that did is stood in for by a session ROW carrying
    # the factor and a token naming it — a passkey ceremony cannot run here,
    # but the guard and the removal below see a real row.
    assert client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).status_code == 401
    from app.platform.services import auth_sessions

    tid = uuid.UUID(dealership)
    row = auth_sessions.create(
        db_session, user_id=uuid.UUID(anna["id"]), dealership_id=tid, device_summary="test", ip_address="", amr=("pwd", "hwk", "mfa")
    )
    db_session.commit()
    cookie = create_access_token(
        user_id=uuid.UUID(anna["id"]),
        tenant_id=tid,
        group_id=uuid.uuid4(),
        roles={AccessRole.SALES},
        amr=("pwd", "hwk", "mfa"),
        mfa=True,
        session_id=row.id,
        expires_at=row.absolute_expires_at,
    )

    status = client.get("/v1/auth/mfa", headers=cookie_headers(cookie)).json()
    assert [p["label"] for p in status["passkeys"]] == ["Laptop"]
    assert status["enrolmentRequired"] is False

    first = login.login(anna).json()
    assert first["factors"] == ["passkey", "recovery_code"]
    options = client.post("/v1/auth/mfa/passkey/options", json={"challengeId": first["challengeId"]})
    assert options.status_code == 200, options.text
    parsed = json.loads(options.json()["options"])
    assert parsed["rpId"] == "localhost"
    assert [c["id"] for c in parsed["allowCredentials"]] == [credential_id]
    assert parsed["challenge"] == _b64url(hashlib.sha256(f"{first['challengeId']}:authn".encode()).digest())

    bogus = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "authenticatorData": "AA", "signature": "AA"},
    }
    refused = client.post(
        "/v1/auth/mfa/passkey/verify", json={"challengeId": first["challengeId"], "credential": bogus}
    )
    assert refused.status_code == 401, refused.text
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) == 1
    unknown = {**bogus, "id": "bm90LW1pbmU", "rawId": "bm90LW1pbmU"}
    assert (
        client.post(
            "/v1/auth/mfa/passkey/verify", json={"challengeId": first["challengeId"], "credential": unknown}
        ).status_code
        == 401
    )

    passkey_id = status["passkeys"][0]["id"]
    assert client.delete(f"/v1/auth/mfa/passkeys/{uuid.uuid4()}", headers=cookie_headers(cookie)).status_code == 404
    assert client.delete(f"/v1/auth/mfa/passkeys/{passkey_id}", headers=cookie_headers(cookie)).status_code == 204
    db_session.refresh(row)
    assert "mfa" not in row.amr and "hwk" not in row.amr, "the last factor left the session row"
    assert login.login(anna).json()["mfaRequired"] is False


# --- the manager's view ---------------------------------------------------------------------


def test_the_auth_event_stream_is_the_managers_to_read_and_nobody_elses(client, login):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = login.session_cookie(manager)
    beat = create_user(client, dealership, isDealerManager=False)
    beats = login.session_cookie(beat)

    response = client.get(f"/v1/dealerships/{dealership}/auth-events", headers=cookie_headers(managers))
    assert response.status_code == 200, response.text
    kinds = [item["kind"] for item in response.json()["items"]]
    assert "login_succeeded" in kinds and "invitation_issued" in kinds and "invitation_accepted" in kinds
    assert response.json()["items"][0]["occurredAt"] >= response.json()["items"][-1]["occurredAt"]
    assert "argon2" not in response.text and STRONG_PASSWORD not in response.text

    assert client.get(f"/v1/dealerships/{dealership}/auth-events", headers=cookie_headers(beats)).status_code == 403
    other = create_dealership(client)
    assert client.get(f"/v1/dealerships/{other}/auth-events", headers=cookie_headers(managers)).status_code == 404
    assert client.get(f"/v1/dealerships/{dealership}/auth-events", headers=platform_admin_headers()).status_code == 200
