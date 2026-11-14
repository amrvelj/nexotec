"""KAN-196 — the local password front door's refusal policy (Authentication &
Identity FR-AU-07/08/09/16): one 401 body and the same argon2 work for every
refusal; per-account backoff and soft lockout, cleared by a reset; per-IP
backoff; rehash on login when the stored parameters fall below policy.
"""

import datetime as dt
import itertools

import argon2
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from app.core.base import utcnow
from app.platform.identity.models.auth_event import AuthEvent, AuthEventKind
from app.platform.identity.models.credential import Credential
from app.platform.identity.services import password_hashing
from app.platform.identity.services.identity import INVALID_CREDENTIALS
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    create_dealership,
    create_user,
    last_mail_to,
    platform_admin_headers,
    token_in,
)

_ips = itertools.count(1)


def _fresh_ip() -> str:
    n = next(_ips)
    return f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"


def _attempt(client, email: str, password: str, *, ip: str | None = None):
    headers = {"X-Forwarded-For": ip or _fresh_ip()}
    return client.post("/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def _refused(response) -> dict:
    assert response.status_code == 401, response.text
    assert "dms_session=" not in response.headers.get("set-cookie", "")
    return response.json()


def _events(db_session, kind: AuthEventKind) -> list[AuthEvent]:
    return list(db_session.scalars(select(AuthEvent).where(AuthEvent.kind == kind)).all())


def _fail_patiently(client, db_session, user: dict, times: int) -> None:
    """`times` wrong passwords with the account backoff waited out between
    them (the last failure is moved an hour back), as an attacker with
    patience would — the only way to reach the lock."""

    for _ in range(times):
        _refused(_attempt(client, user["email"], "not-the-password-at-all"))
        credential = db_session.scalar(select(Credential).where(Credential.user_id == user["id"]))
        db_session.refresh(credential)
        credential.last_failed_at = credential.last_failed_at - dt.timedelta(hours=1)
        db_session.commit()


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def test_every_refusal_answers_the_same_body(client, login, db_session):
    """Unknown address, wrong password, locked credential, inactive account:
    the response says nothing about which it was (FR-AU-16)."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    beat = create_user(client, dealership, isDealerManager=False, firstName="Beat")
    login.accept_invitation(beat)
    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{beat['id']}",
        json={"status": "suspended"},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    assert response.status_code == 200, response.text
    carla = create_user(client, dealership, isDealerManager=False, firstName="Carla")
    login.accept_invitation(carla)
    _fail_patiently(client, db_session, carla, times=5)

    bodies = {
        "unknown": _refused(_attempt(client, "nobody@example.ch", STRONG_PASSWORD)),
        # KAN-387: a NUL names no account — the same 401, never a 500.
        "nul_in_address": _refused(_attempt(client, anna["email"] + "\u0000", STRONG_PASSWORD)),
        "wrong_password": _refused(_attempt(client, anna["email"], "not-the-password-at-all")),
        "inactive": _refused(_attempt(client, beat["email"], STRONG_PASSWORD)),
        "locked": _refused(_attempt(client, carla["email"], STRONG_PASSWORD)),
        "invited_without_password": _refused(
            _attempt(client, create_user(client, dealership, firstName="Dora")["email"], STRONG_PASSWORD)
        ),
    }
    assert len({str(body) for body in bodies.values()}) == 1, bodies
    assert bodies["unknown"]["error"]["message"] == INVALID_CREDENTIALS
    # Every refusal is in the event stream, with its real reason — for staff.
    assert len(_events(db_session, AuthEventKind.LOGIN_FAILED)) >= 7
    assert len(_events(db_session, AuthEventKind.LOCKED_OUT)) == 1


def test_every_refusal_does_the_same_argon2_work_whether_or_not_the_account_exists(
    client, login, db_session, monkeypatch
):
    """FR-AU-07: a dummy argon2 verify runs when the address names no
    account (and when an account is locked), so the timing does not
    enumerate accounts. Proved by the work itself, not by the wall clock
    (KAN-444: a median-ratio timing test failed whenever the machine was
    busy): every refusal runs exactly one argon2 verify, at the same cost
    parameters as the account's own stored hash."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))

    verifies: list[argon2.Parameters] = []
    real_verify = password_hashing.verify_password

    def counting_verify(password_hash: str, password: str) -> bool:
        verifies.append(argon2.extract_parameters(password_hash))
        return real_verify(password_hash, password)

    monkeypatch.setattr(password_hashing, "verify_password", counting_verify)

    def work_of(email: str) -> list[argon2.Parameters]:
        verifies.clear()
        _refused(_attempt(client, email, "not-the-password-at-all"))
        return list(verifies)

    def throttled_refusals() -> int:
        return len(_events(db_session, AuthEventKind.LOGIN_THROTTLED))

    def backoff_waited_out() -> None:
        db_session.refresh(credential)
        credential.last_failed_at = utcnow() - dt.timedelta(hours=1)
        db_session.commit()

    expected = [argon2.extract_parameters(credential.password_hash)]
    # The account exists and the password is wrong: its own hash is verified.
    assert work_of(anna["email"]) == expected
    # No account at all: the dummy verify.
    assert work_of("nobody@example.ch") == expected
    # From the third failure on the account backs off: an attempt inside the
    # backoff is refused before its own verify, so the dummy one runs. The
    # last failure is put in the future, so "inside" never depends on how
    # fast this machine gets to the next request.
    backoff_waited_out()
    _fail_patiently(client, db_session, anna, times=1)
    assert work_of(anna["email"]) == expected  # the third failure
    db_session.refresh(credential)
    credential.last_failed_at = utcnow() + dt.timedelta(hours=1)
    db_session.commit()
    throttled_before = throttled_refusals()
    assert work_of(anna["email"]) == expected
    assert throttled_refusals() == throttled_before + 1
    # Locked: the same.
    backoff_waited_out()
    _fail_patiently(client, db_session, anna, times=2)
    db_session.refresh(credential)
    assert credential.locked_until is not None
    assert work_of(anna["email"]) == expected


def test_five_failures_lock_the_credential_and_a_reset_clears_the_lock(client, login, mailbox, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)

    _fail_patiently(client, db_session, anna, times=5)

    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    db_session.refresh(credential)
    assert credential.failed_login_attempts == 5
    assert credential.locked_until is not None
    # FR-AU-10: the manager sees the lock, never a password.
    status = client.get(
        f"/v1/dealerships/{dealership}/users/{anna['id']}/login-status", headers=platform_admin_headers()
    )
    assert status.status_code == 200, status.text
    assert status.json()["hasPassword"] is True
    assert status.json()["lockedUntil"] is not None
    assert status.json()["failedLoginAttempts"] == 5
    assert "argon2" not in status.text
    # The right password is refused while the lock holds.
    _refused(_attempt(client, anna["email"], STRONG_PASSWORD))

    response = client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})
    assert response.status_code == 202, response.text
    token = token_in(last_mail_to(mailbox, anna["email"]))
    new_password = "a-brand-new-password-2026"
    response = client.post("/v1/auth/password-reset/confirm", json={"token": token, "password": new_password})
    assert response.status_code == 204, response.text

    response = _attempt(client, anna["email"], new_password)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert credential.failed_login_attempts == 0
    assert credential.locked_until is None


def test_a_success_resets_the_failure_count_before_the_lock(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    for _ in range(2):
        _refused(_attempt(client, anna["email"], "not-the-password-at-all"))

    assert _attempt(client, anna["email"], STRONG_PASSWORD).status_code == 200
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert credential.failed_login_attempts == 0


def test_the_account_backs_off_from_the_third_failure(client, login, db_session):
    """FR-AU-08: two failures cost nothing; the third starts the exponential
    backoff — the right password is refused for the next two seconds."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    for _ in range(3):
        _refused(_attempt(client, anna["email"], "not-the-password-at-all"))

    _refused(_attempt(client, anna["email"], STRONG_PASSWORD))
    assert len(_events(db_session, AuthEventKind.LOGIN_THROTTLED)) == 1


def test_failures_from_one_address_throttle_every_account_behind_it(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    attacker = _fresh_ip()
    for i in range(3):
        _refused(_attempt(client, f"guess-{i}@example.ch", "not-the-password-at-all", ip=attacker))

    _refused(_attempt(client, anna["email"], STRONG_PASSWORD, ip=attacker))
    throttled = _events(db_session, AuthEventKind.LOGIN_THROTTLED)
    assert [event.ip_address for event in throttled] == [attacker]
    # The same account from elsewhere is untouched: the axes are independent.
    assert _attempt(client, anna["email"], STRONG_PASSWORD).status_code == 200


def test_the_last_forwarded_address_is_the_one_the_throttle_sees(client, login, db_session):
    """Behind the platform proxy the client may write its own X-Forwarded-For
    entry; the proxy appends the real one last, and that is the one counted."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    _refused(
        client.post(
            "/v1/auth/login",
            json={"email": anna["email"], "password": "not-the-password-at-all"},
            headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.7"},
        )
    )
    failed = _events(db_session, AuthEventKind.LOGIN_FAILED)
    assert [event.ip_address for event in failed] == ["198.51.100.7"]


def test_a_login_rehashes_a_credential_stored_below_the_current_policy(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(STRONG_PASSWORD)
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    credential.password_hash = weak
    db_session.commit()

    assert _attempt(client, anna["email"], STRONG_PASSWORD).status_code == 200

    db_session.expire_all()
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert credential.password_hash != weak
    assert credential.password_hash.startswith("$argon2id$")
    assert "m=65536,t=3,p=4" in credential.password_hash
    assert len(_events(db_session, AuthEventKind.CREDENTIAL_REHASHED)) == 1


def test_an_address_signs_in_whatever_its_case(client, login, mailbox):
    """Stored lower-cased, resolved case-insensitively: the address a user
    types in any case is the one the invitation went to."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership, email="Max.Muster@Example.CH")
    assert anna["email"] == "max.muster@example.ch"
    login.accept_invitation(anna)

    assert _attempt(client, "MAX.MUSTER@example.ch", STRONG_PASSWORD).status_code == 200
    sent_before = len(mailbox)
    assert client.post("/v1/auth/password-reset/request", json={"email": "Max.Muster@Example.CH"}).status_code == 202
    assert len(mailbox) == sent_before + 1


def test_no_password_in_clear_is_ever_stored(client, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert STRONG_PASSWORD not in credential.password_hash
    assert credential.password_hash.startswith("$argon2id$")
    assert credential.password_history == []
