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

def _cose_p256_public_key() -> str:
    """A real COSE EC2/P-256 key, base64url — what a registration stores. The
    verify path decodes the stored key before judging the answer, so a stored
    key has to be decodable even where the answer is bogus."""

    from cryptography.hazmat.primitives.asymmetric import ec
    from webauthn.helpers import encode_cbor

    numbers = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    cose = {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
    return _b64url(encode_cbor(cose))


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
            public_key=_cose_p256_public_key(),
            label="Laptop",
        )
    )
    db_session.commit()
    # The password-only session never proved the passkey and has ended (the
    # guard). A session that did is stood in for by a session ROW carrying
    # the factor and a token naming it (the real ceremony has its own test
    # below), so the guard and the removal below see a real row.
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


def test_a_malformed_passkey_answer_is_a_refusal_whichever_exception_the_library_raises(
    client, login, db_session, monkeypatch
):
    """CI's resolver picked `webauthn` 2.7.1, which raises `InvalidJSONStructure`
    unwrapped for a bogus `clientDataJSON` where 3.0.1 wraps it in
    `InvalidAuthenticationResponse` — and the verify route answered 500. Every
    library refusal is a wrong answer (401 at sign-in, 422 at registration)."""

    from webauthn.helpers.exceptions import InvalidJSONStructure

    from app.platform.models.auth_session import AuthSession
    from app.platform.services import mfa as mfa_service

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    row = db_session.scalars(
        select(AuthSession).where(AuthSession.user_id == uuid.UUID(anna["id"]), AuthSession.revoked_at.is_(None))
    ).one()
    credential_id = _b64url(b"laptop-key")
    db_session.add(
        MfaPasskey(
            user_id=uuid.UUID(anna["id"]),
            credential_id=credential_id,
            public_key=_cose_p256_public_key(),
            sign_count=0,
            label="Laptop",
            transports=[],
        )
    )
    row.amr = ["pwd", "hwk", "mfa"]
    db_session.commit()

    def unwrapped(**kwargs):
        raise InvalidJSONStructure('client_data_json missing required property "type"')

    monkeypatch.setattr(mfa_service, "verify_authentication_response", unwrapped)
    monkeypatch.setattr(mfa_service, "verify_registration_response", unwrapped)

    first = login.login(anna).json()
    bogus = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "authenticatorData": "AA", "signature": "AA"},
    }
    refused = client.post("/v1/auth/mfa/passkey/verify", json={"challengeId": first["challengeId"], "credential": bogus})
    assert refused.status_code == 401, refused.text
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) == 1

    options = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie))
    assert options.status_code == 200, options.text
    registered = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": options.json()["challengeId"], "label": "Phone", "credential": bogus},
        headers=cookie_headers(cookie),
    )
    assert registered.status_code == 422, registered.text


def _passkey_holder(client, login, db_session) -> tuple[dict, str, str]:
    """Anna with one passkey on file and a factor-backed session: returns her,
    the stored credential id and her session cookie."""

    from app.platform.models.auth_session import AuthSession

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    row = db_session.scalars(
        select(AuthSession).where(AuthSession.user_id == uuid.UUID(anna["id"]), AuthSession.revoked_at.is_(None))
    ).one()
    credential_id = _b64url(b"laptop-key")
    db_session.add(
        MfaPasskey(
            user_id=uuid.UUID(anna["id"]),
            credential_id=credential_id,
            public_key=_cose_p256_public_key(),
            sign_count=0,
            label="Laptop",
            transports=[],
        )
    )
    row.amr = ["pwd", "hwk", "mfa"]
    db_session.commit()
    return anna, credential_id, cookie


def _client_data(**overrides) -> str:
    data = {"type": "webauthn.get", "challenge": "AA", "origin": "http://localhost:5173", **overrides}
    return _b64url(json.dumps(data).encode())


MALFORMED_ANSWERS = {
    "clientDataJSON is a JSON number": {"response": {"clientDataJSON": _b64url(b"123")}},
    "clientDataJSON is JSON null": {"response": {"clientDataJSON": _b64url(b"null")}},
    "clientDataJSON is not UTF-8": {"response": {"clientDataJSON": _b64url(b"\xff\xfe{}")}},
    "clientDataJSON is not JSON": {"response": {"clientDataJSON": _b64url(b"{not json")}},
    "challenge is not a string": {"response": {"clientDataJSON": _client_data(challenge=5)}},
    "type is not a string": {"response": {"clientDataJSON": _client_data(type=None)}},
    "authenticatorData is not a string": {"response": {"authenticatorData": 7}},
    "response is not an object": {"response": "x"},
    "rawId is a number": {"rawId": 5, "id": 5},
    "rawId is an object": {"rawId": {"a": 1}, "id": {"a": 1}},
    # 2.7.1 decodes userHandle outside its own try (binascii.Error / ValueError)
    "userHandle is not base64url": {"response": {"userHandle": "A"}},
    "userHandle is not ASCII": {"response": {"userHandle": "é"}},
    "userHandle is not a string": {"response": {"userHandle": 7}},
    # json.loads recursion: a RecursionError, not a ValueError
    "clientDataJSON is nested ten thousand deep": {"response": {"clientDataJSON": _b64url(b"[" * 10_000 + b"]" * 10_000)}},
    "clientDataJSON is absurdly long": {"response": {"clientDataJSON": _client_data(padding="x" * 5000)}},
}


@pytest.mark.parametrize("defect", list(MALFORMED_ANSWERS))
def test_a_malformed_passkey_answer_through_the_real_library_is_a_wrong_answer(client, login, db_session, defect):
    """`webauthn` 2.7.1 — the version the `cryptography<46` pin installs — lets
    these shapes reach `json.loads` and dict indexing unguarded (builtin
    exceptions, not `WebAuthnException`); the tenth review found the verify
    route answering 500 to them, outside the wrong-answer counting. Every one
    is a 401 that counts (login) and a 422 (registration)."""

    anna, credential_id, cookie = _passkey_holder(client, login, db_session)
    override = MALFORMED_ANSWERS[defect]

    def shaped(response: dict) -> dict:
        credential = {"id": credential_id, "rawId": credential_id, "type": "public-key", "response": response}
        if isinstance(override.get("response"), dict):
            credential["response"] = {**response, **override["response"]}
        else:
            credential.update(override)
        return credential

    answer = shaped(
        {"clientDataJSON": _client_data(), "authenticatorData": _b64url(b"\x00" * 37), "signature": "AA"}
    )

    first = login.login(anna).json()
    refused = client.post("/v1/auth/mfa/passkey/verify", json={"challengeId": first["challengeId"], "credential": answer})
    assert refused.status_code == 401, refused.text
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) == 1

    options = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie))
    assert options.status_code == 200, options.text
    registration = shaped({"clientDataJSON": _client_data(type="webauthn.create"), "attestationObject": _b64url(b"\xa0")})
    registered = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": options.json()["challengeId"], "label": "Phone", "credential": registration},
        headers=cookie_headers(cookie),
    )
    assert registered.status_code == 422, registered.text


@pytest.mark.parametrize(
    "attestation",
    [b"\x01", b"\xa1\x63fmt\x64none", b"\xa2\x63fmt\x05\x68authData\x40", b"\xa2\x63fmt\x64none\x68authData\x05"],
    ids=["not a map", "no authData", "fmt not a string", "authData not bytes"],
)
def test_a_malformed_attestation_object_is_refused_before_the_library(client, login, db_session, attestation):
    """2.7.1's `parse_attestation_object` indexes the decoded CBOR unguarded
    (KeyError / TypeError); the shape is checked before the library sees it."""

    from app.platform.services import mfa as mfa_service

    _anna, credential_id, cookie = _passkey_holder(client, login, db_session)
    options = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie))
    # The real challenge, so 2.7.1 gets past its challenge comparison and
    # would reach the attestation parser if the shape check let it.
    challenge = _b64url(mfa_service.challenge_for(options.json()["challengeId"], "register"))
    answer = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": _client_data(type="webauthn.create", challenge=challenge),
            "attestationObject": _b64url(attestation),
        },
    }
    registered = client.post(
        "/v1/auth/mfa/passkeys",
        json={"challengeId": options.json()["challengeId"], "label": "Phone", "credential": answer},
        headers=cookie_headers(cookie),
    )
    assert registered.status_code == 422, registered.text


def test_a_stored_passkey_the_library_cannot_decode_is_a_server_defect_not_a_wrong_answer(client, login, db_session):
    """The stored key is ours: a row the library can no longer decode (a
    library change, a corrupt write) surfaces as an exception — never as the
    user's `mfa_failed` counting towards lockout (tenth review)."""

    from webauthn.helpers.exceptions import WebAuthnException

    from app.platform.models.user import User
    from app.platform.services import mfa as mfa_service

    anna, credential_id, _cookie = _passkey_holder(client, login, db_session)
    stored = db_session.scalars(select(MfaPasskey).where(MfaPasskey.credential_id == credential_id)).one()
    stored.public_key = _b64url(b"not-a-cose-key")
    db_session.commit()
    user = db_session.get(User, uuid.UUID(anna["id"]))
    well_formed = {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {"clientDataJSON": _client_data(), "authenticatorData": _b64url(b"\x00" * 37), "signature": "AA"},
    }
    with pytest.raises(WebAuthnException):
        mfa_service.verify_passkey(db_session, user=user, credential=well_formed, challenge=b"x")


def _verified_registration(public_key: bytes):
    """What `verify_registration_response` hands back for a `none` attestation:
    the library decodes the COSE key and checks `alg`, nothing more."""

    from types import SimpleNamespace

    return SimpleNamespace(credential_id=b"phone-key", credential_public_key=public_key, sign_count=0)


def test_a_passkey_whose_key_cannot_be_loaded_is_refused_at_registration(client, login, db_session, monkeypatch):
    """A `none` attestation lets a COSE key through that decodes but cannot be
    loaded (a point off the curve, integer coordinates): stored, it would count
    as a factor nobody can use and fail every sign-in (eleventh review). The
    key is loaded at registration; a bad one is a 422."""

    from webauthn.helpers import encode_cbor

    from app.core.errors import UnprocessableEntityError
    from app.platform.models.user import User
    from app.platform.services import mfa as mfa_service

    anna, _credential_id, _cookie = _passkey_holder(client, login, db_session)
    user = db_session.get(User, uuid.UUID(anna["id"]))
    off_curve = encode_cbor({1: 2, 3: -7, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32})
    integer_coordinates = encode_cbor({1: 2, 3: -7, -1: 1, -2: 5, -3: 6})
    well_formed = {
        "id": "cGhvbmUta2V5",
        "rawId": "cGhvbmUta2V5",
        "type": "public-key",
        "response": {"clientDataJSON": _client_data(type="webauthn.create"), "attestationObject": _b64url(b"\xa3cfmtdnonegattStmt\xa0hauthData\x40")},
    }
    for bad_key in (off_curve, integer_coordinates):
        monkeypatch.setattr(
            mfa_service, "verify_registration_response", lambda key=bad_key, **kw: _verified_registration(key)
        )
        with pytest.raises(UnprocessableEntityError):
            mfa_service.register_passkey(
                db_session, user=user, credential=well_formed, challenge=b"x", label="Phone", ip_address="127.0.0.1"
            )
        db_session.rollback()
    assert db_session.scalars(select(MfaPasskey).where(MfaPasskey.credential_id == "cGhvbmUta2V5")).first() is None


def test_only_known_transport_names_are_stored(client, login, db_session, monkeypatch):
    """`response.transports` is client-supplied: a string would be stored as
    its letters, a number was a 500 after the library had accepted the
    attestation (eleventh review). Only the spec's names, as strings, stay."""

    from app.platform.models.user import User
    from app.platform.services import mfa as mfa_service

    anna, _credential_id, _cookie = _passkey_holder(client, login, db_session)
    user = db_session.get(User, uuid.UUID(anna["id"]))
    good_key = base64.urlsafe_b64decode(_cose_p256_public_key() + "==")
    monkeypatch.setattr(mfa_service, "verify_registration_response", lambda **kw: _verified_registration(good_key))
    for transports, stored in (("usb", []), (5, []), (["usb", 5, {"a": 1}, "nfc", "teleport"], ["usb", "nfc"])):
        credential = {
            "id": "cGhvbmUta2V5",
            "rawId": "cGhvbmUta2V5",
            "type": "public-key",
            "response": {
                "clientDataJSON": _client_data(type="webauthn.create"),
                "attestationObject": _b64url(b"\xa3cfmtdnonegattStmt\xa0hauthData\x40"),
                "transports": transports,
            },
        }
        row = mfa_service.register_passkey(
            db_session, user=user, credential=credential, challenge=b"x", label="Phone", ip_address="127.0.0.1"
        )
        assert row.transports == stored, transports
        db_session.delete(row)
        db_session.flush()


# --- real ceremonies through the library -----------------------------------------------------


def _settings():
    from app.core.config import get_settings

    return get_settings()


def _auth_data(*, flags: int, sign_count: int, credential_id: bytes | None = None, cose_key: bytes | None = None) -> bytes:
    data = hashlib.sha256(_settings().webauthn_rp_id.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")
    if credential_id is not None and cose_key is not None:
        data += b"\x00" * 16 + len(credential_id).to_bytes(2, "big") + credential_id + cose_key
    return data


def _cose_ec2(private_key, alg: int = -7) -> bytes:
    from webauthn.helpers import encode_cbor

    numbers = private_key.public_key().public_numbers()
    return encode_cbor({1: 2, 3: alg, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})


def _cose_rsa(private_key, alg: int) -> bytes:
    from webauthn.helpers import encode_cbor

    numbers = private_key.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    return encode_cbor({1: 3, 3: alg, -1: n, -2: numbers.e.to_bytes(3, "big")})


def _register_through_the_library(client, cookie: str, credential_id: bytes, cose_key: bytes, *, sign_count: int = 0):
    """A `none` attestation exactly as a platform authenticator would answer the
    registration options, through the real `webauthn` library."""

    from webauthn.helpers import encode_cbor

    options = client.post("/v1/auth/mfa/passkeys/options", headers=cookie_headers(cookie))
    assert options.status_code == 200, options.text
    parsed = json.loads(options.json()["options"])
    client_data = json.dumps(
        {"type": "webauthn.create", "challenge": parsed["challenge"], "origin": _settings().webauthn_origin}
    ).encode()
    attestation = encode_cbor(
        {
            "fmt": "none",
            "attStmt": {},
            "authData": _auth_data(flags=0x41, sign_count=sign_count, credential_id=credential_id, cose_key=cose_key),
        }
    )
    cred_id = _b64url(credential_id)
    return client.post(
        "/v1/auth/mfa/passkeys",
        json={
            "challengeId": options.json()["challengeId"],
            "label": "Laptop",
            "credential": {
                "id": cred_id,
                "rawId": cred_id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": _b64url(client_data),
                    "attestationObject": _b64url(attestation),
                    "transports": ["internal"],
                },
            },
        },
        headers=cookie_headers(cookie),
    )


def _sign_in_through_the_library(client, login, anna: dict, private_key, credential_id: bytes, *, sign_count: int):
    """The second step answered with a real ECDSA signature over the
    authenticator data and the client-data hash."""

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    first = login.login(anna).json()
    assert "passkey" in first["factors"], first
    options = client.post("/v1/auth/mfa/passkey/options", json={"challengeId": first["challengeId"]})
    assert options.status_code == 200, options.text
    parsed = json.loads(options.json()["options"])
    client_data = json.dumps(
        {"type": "webauthn.get", "challenge": parsed["challenge"], "origin": _settings().webauthn_origin}
    ).encode()
    auth_data = _auth_data(flags=0x01, sign_count=sign_count)
    signed = auth_data + hashlib.sha256(client_data).digest()
    signature = private_key.sign(signed) if isinstance(private_key, Ed25519PrivateKey) else private_key.sign(
        signed, ec.ECDSA(hashes.SHA256())
    )
    cred_id = _b64url(credential_id)
    answer = {
        "id": cred_id,
        "rawId": cred_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64url(client_data),
            "authenticatorData": _b64url(auth_data),
            "signature": _b64url(signature),
            "userHandle": None,
        },
    }
    payload = {"challengeId": first["challengeId"], "credential": answer}
    return client.post("/v1/auth/mfa/passkey/verify", json=payload), payload


def test_a_real_passkey_ceremony_registers_and_signs_in(client, login, db_session):
    """Registration and sign-in through the real library with a real P-256 key
    and signature: the committed suite completes a WebAuthn ceremony. A replay
    of the spent challenge and a stale signature counter are refused."""

    from cryptography.hazmat.primitives.asymmetric import ec

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    assert client.get("/v1/customers", headers=cookie_headers(cookie)).status_code == 403  # owes a factor
    private_key = ec.generate_private_key(ec.SECP256R1())
    credential_id = b"laptop-" + uuid.uuid4().bytes

    registered = _register_through_the_library(client, cookie, credential_id, _cose_ec2(private_key))
    assert registered.status_code == 201, registered.text
    assert registered.json()["label"] == "Laptop"
    assert client.get("/v1/customers", headers=cookie_headers(cookie)).status_code == 200  # the row proved it
    stored = db_session.scalars(select(MfaPasskey).where(MfaPasskey.credential_id == _b64url(credential_id))).one()
    assert stored.transports == ["internal"] and stored.sign_count == 0

    signed_in, payload = _sign_in_through_the_library(client, login, anna, private_key, credential_id, sign_count=7)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["session"]["mfaEnrolmentRequired"] is False
    assert sorted(decode_token_claims(signed_in.cookies.get("dms_session"))["amr"]) == ["hwk", "mfa", "pwd"]
    db_session.expire_all()
    assert stored.sign_count == 7 and stored.last_used_at is not None
    assert _events(db_session, AuthEventKind.MFA_FAILED) == []
    replayed = client.post("/v1/auth/mfa/passkey/verify", json=payload)  # the same signed answer, spent challenge
    assert replayed.status_code == 401 and replayed.json()["error"]["details"] == {"step": "expired"}, replayed.text

    stale, _payload = _sign_in_through_the_library(client, login, anna, private_key, credential_id, sign_count=7)
    assert stale.status_code == 401, stale.text
    assert len(_events(db_session, AuthEventKind.MFA_FAILED)) == 1


def test_a_key_outside_the_passkey_key_policy_is_refused_at_registration(client, login, db_session):
    """Through the real library: a `none` attestation accepts any key that
    decodes, so the key policy judges it (twelfth to fourteenth reviews). Refused
    with 422: an RSA key under the 2048-bit floor (policy — the library would
    verify it), an EC2 key with an RSA algorithm and an RSA key with ECDSA (no
    signature ever verifies), an even RSA modulus and one past 16384 bits
    (OpenSSL never verifies them), an exponent past 64 bits (policy), an
    Ed25519 encoding that is not a curve point, a credential id past the
    spec's 1023 bytes. Accepted: a 2048-bit RS256 key and a real Ed25519 key,
    which then signs in."""

    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from webauthn.helpers import encode_cbor

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)

    small_rsa = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    big_rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p256 = ec.generate_private_key(ec.SECP256R1())
    big = big_rsa.public_key().public_numbers()
    even_modulus = encode_cbor({1: 3, 3: -257, -1: (big.n - 1).to_bytes(256, "big"), -2: b"\x01\x00\x01"})
    huge_exponent = encode_cbor({1: 3, 3: -257, -1: big.n.to_bytes(256, "big"), -2: (2**64 + 1).to_bytes(9, "big")})
    not_a_point = encode_cbor({1: 1, 3: -8, -1: 6, -2: (2).to_bytes(32, "little")})
    past_the_ceiling = encode_cbor({1: 3, 3: -257, -1: ((1 << 16384) | 1).to_bytes(2049, "big"), -2: b"\x01\x00\x01"})
    for cose_key in (
        _cose_rsa(small_rsa, alg=-39),
        _cose_ec2(p256, alg=-257),
        _cose_rsa(big_rsa, alg=-7),
        even_modulus,
        huge_exponent,
        not_a_point,
        past_the_ceiling,
    ):
        refused = _register_through_the_library(client, cookie, b"key-" + uuid.uuid4().bytes, cose_key)
        assert refused.status_code == 422, refused.text
    too_long = _register_through_the_library(client, cookie, bytes(1024), _cose_ec2(p256))
    assert too_long.status_code == 422, too_long.text
    assert db_session.scalars(select(MfaPasskey)).all() == []
    accepted = _register_through_the_library(client, cookie, b"key-" + uuid.uuid4().bytes, _cose_rsa(big_rsa, alg=-257))
    assert accepted.status_code == 201, accepted.text

    ed25519 = Ed25519PrivateKey.generate()
    ed_credential = b"ed-" + uuid.uuid4().bytes
    real_point = encode_cbor({1: 1, 3: -8, -1: 6, -2: ed25519.public_key().public_bytes_raw()})
    assert _register_through_the_library(client, cookie, ed_credential, real_point).status_code == 201
    signed_in, _payload = _sign_in_through_the_library(client, login, anna, ed25519, ed_credential, sign_count=1)
    assert signed_in.status_code == 200, signed_in.text


def test_the_counter_and_credential_id_take_every_value_the_spec_allows(client, login, db_session):
    """signCount is an unsigned 32-bit counter and a credential id is up to
    1023 bytes (1364 characters base64url); a signed 32-bit column and a
    1024-character column refused both with a 500 (twelfth review)."""

    from cryptography.hazmat.primitives.asymmetric import ec

    dealership = create_dealership(client, mfa_enforced=True)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    private_key = ec.generate_private_key(ec.SECP256R1())
    credential_id = bytes(range(256)) * 3 + bytes(255)  # 1023 bytes
    registered = _register_through_the_library(client, cookie, credential_id, _cose_ec2(private_key), sign_count=2**31)
    assert registered.status_code == 201, registered.text
    stored = db_session.scalars(select(MfaPasskey).where(MfaPasskey.credential_id == _b64url(credential_id))).one()
    assert stored.sign_count == 2**31 and len(stored.credential_id) == 1364

    signed_in, _payload = _sign_in_through_the_library(client, login, anna, private_key, credential_id, sign_count=2**32 - 1)
    assert signed_in.status_code == 200, signed_in.text
    db_session.expire_all()
    assert stored.sign_count == 2**32 - 1


def test_client_data_past_the_cap_is_a_wrong_answer_before_any_parsing():
    """`CLIENT_DATA_MAX_BYTES` is what keeps a pathological `clientDataJSON`
    away from `json.loads`: one byte over is refused, the cap itself passes."""

    from app.platform.services import mfa as mfa_service

    def answer(padding: int) -> dict:
        data = {"type": "webauthn.get", "challenge": "AA", "origin": "http://localhost:5173", "pad": ""}
        data["pad"] = "x" * (padding - len(json.dumps(data).encode()))
        raw = json.dumps(data).encode()
        assert len(raw) == padding
        return {
            "id": "AQID",
            "rawId": "AQID",
            "type": "public-key",
            "response": {"clientDataJSON": _b64url(raw), "authenticatorData": "AA", "signature": "AA"},
        }

    fields = ("authenticatorData", "signature")
    assert mfa_service._well_formed_client_answer(answer(mfa_service.CLIENT_DATA_MAX_BYTES), response_fields=fields)
    assert not mfa_service._well_formed_client_answer(
        answer(mfa_service.CLIENT_DATA_MAX_BYTES + 1), response_fields=fields
    )


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
