"""KAN-196 — second factors (D-A-09, D-A-36, A-13, FR-AU-11/12): TOTP
enrolment and the login step, replay refused, recovery codes once each,
passkey options bound to a single-use challenge, platform staff refused on a
gated route without a factor in the session, the Betrieb's enforcement
switch, a manager's removal naming the actor, the auth-event stream.
"""

import base64
import hashlib
import json
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
    dealership = create_dealership(client)
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
    dealership = create_dealership(client)
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


# --- enforcement ------------------------------------------------------------------------


def test_the_betrieb_decides_enforcement_and_platform_staff_are_always_enforced(client, login, db_session):
    dealership = create_dealership(client)
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
    updated = client.put(
        f"/v1/dealerships/{dealership}/auth-config",
        json={"mfaEnforced": False},
        headers={**cookie_headers(cookie), "If-Match": "1"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {"authMethod": "local", "mfaEnforced": False, "version": 2}
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


def test_a_manager_removes_a_users_factors_and_the_event_names_the_actor(client, login, db_session):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    anna = create_user(client, dealership, isDealerManager=False)
    _enrol_totp(client, login.session_cookie(anna))
    assert login.login(anna).json()["mfaRequired"] is True

    response = client.delete(
        f"/v1/dealerships/{dealership}/users/{anna['id']}/mfa", headers=cookie_headers(login.session_cookie(manager))
    )
    assert response.status_code == 204, response.text

    removed = _events(db_session, AuthEventKind.MFA_REMOVED)
    assert len(removed) == 1
    assert str(removed[0].actor_id) == manager["id"]
    assert removed[0].details == {"factors": ["totp"], "by_self": False, "all": True}
    assert db_session.scalars(select(MfaTotp)).all() == []
    assert db_session.scalars(select(MfaRecoveryCode)).all() == []
    assert login.login(anna).json()["mfaRequired"] is False
    # Not a manager: 403.
    beat = create_user(client, dealership, isDealerManager=False)
    assert (
        client.delete(
            f"/v1/dealerships/{dealership}/users/{anna['id']}/mfa", headers=cookie_headers(login.session_cookie(beat))
        ).status_code
        == 403
    )


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
