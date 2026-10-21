"""KAN-411 — the account routes that took no user-row lock deadlocked
against the routes that do (KAN-200, KAN-380, KAN-400): each answered one of
two requests for the same user with a 500.

- P4: the user's own TOTP removal deleted the authenticator, then the
  recovery codes; platform staff's MFA reset deleted them in its own order.
- P2: removing the last factor ended the caller's other sessions after
  writing its own session row; a staff revoke-all scanned the same rows in
  heap order.
- P1: a reset request ended the live reset token and then inserted a new
  one (which needs the user row), while an e-mail change held the user row
  and then ended the tokens.
- A recovery code spent by the MFA step while staff's reset deleted it.

Every writer of a user-owned auth row now takes the user row first, FOR NO
KEY UPDATE, so the second request waits for the first. Each race is staged
as in `test_platform_concurrent_first_writes.py`: one request is paused
holding its locks, the other starts and must wait on a lock (pg_locks), then
the first is released.
"""

import inspect
import threading
import uuid

import pytest
from sqlalchemy import select

from app.platform.api import auth as auth_api
from app.platform.api import dealerships as dealerships_api
from app.platform.models.auth_session import AuthSession
from app.platform.models.mfa import MfaRecoveryCode, MfaTotp
from app.platform.services import auth_sessions, auth_tokens, mfa
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock
from tests.test_identity_mfa import _enrol_totp


def _race(engine, monkeypatch, *, owner, attribute: str, first, second, after: bool = False) -> dict[str, int]:
    """Runs `first` until it reaches `owner.attribute` (paused just before
    the real call, or just after it with `after`), starts `second`, waits
    until a backend waits on a lock, releases `first`. Returns both status
    codes; a deadlock surfaces as an error from either thread."""

    reached, release = threading.Event(), threading.Event()
    claimed = threading.Lock()
    real = getattr(owner, attribute)

    def _paused(*args, **kwargs):
        mine = claimed.acquire(blocking=False)
        if mine and not after:
            reached.set()
            release.wait(timeout=15)
        result = real(*args, **kwargs)
        if mine and after:
            reached.set()
            release.wait(timeout=15)
        return result

    monkeypatch.setattr(owner, attribute, _paused)
    errors: list[Exception] = []
    codes: dict[str, int] = {}
    one = _run("first", errors, lambda: codes.__setitem__("first", first().status_code))
    two = None
    try:
        assert reached.wait(timeout=10), f"the first request never reached {attribute}"
        two = _run("second", errors, lambda: codes.__setitem__("second", second().status_code))
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        one.join(timeout=20)
        two.join(timeout=20)
    finally:
        release.set()
    assert two is not None and not one.is_alive() and not two.is_alive()
    assert errors == []
    return codes


def _staff_mfa_reset(client, dealership: str, user: dict):
    return client.delete(f"/v1/dealerships/{dealership}/users/{user['id']}/mfa", headers=platform_admin_headers())


def _staff_session_revoke(client, dealership: str, user: dict):
    return client.delete(f"/v1/dealerships/{dealership}/users/{user['id']}/sessions", headers=platform_admin_headers())


# --- P4: self-service TOTP removal vs staff's MFA reset -------------------------------------


def test_removing_ones_own_authenticator_racing_staffs_mfa_reset_is_no_deadlock(engine, client, mailbox, db_session, monkeypatch):
    """The removal is paused after it deleted the authenticator, before the
    recovery codes; staff's reset starts. It used to delete the codes and
    wait for the authenticator while the removal waited for the codes."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)
    _enrol_totp(client, cookie)

    codes = _race(
        engine,
        monkeypatch,
        owner=mfa,
        attribute="_drop_recovery_codes_if_no_factor",
        first=lambda: client.delete("/v1/auth/mfa/totp", headers=cookie_headers(cookie)),
        second=lambda: _staff_mfa_reset(client, dealership, anna),
    )

    assert codes == {"first": 204, "second": 204}
    db_session.expire_all()
    user_id = uuid.UUID(anna["id"])
    assert db_session.scalars(select(MfaTotp).where(MfaTotp.user_id == user_id)).all() == []
    assert db_session.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)).all() == []


# --- P2: removing the last factor vs a revoke-all --------------------------------------------


def test_removing_the_last_factor_racing_staffs_session_revoke_is_no_deadlock(engine, client, mailbox, db_session, monkeypatch):
    """Two sessions: the first enrolled the factor, the second signed in with
    it. The second removes the factor and is paused after it rewrote its own
    session row, before it ends the first; staff's revoke-all starts and
    ended the first before waiting for the second — a deadlock."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login = PasswordLogin(client, mailbox)
    first_cookie = login.session_cookie(anna)
    _secret, recovery_codes = _enrol_totp(client, first_cookie)
    step = login.login(anna)
    assert step.json()["mfaRequired"] is True, step.text
    verified = client.post("/v1/auth/mfa/verify", json={"challengeId": step.json()["challengeId"], "code": recovery_codes[0]})
    assert verified.status_code == 200, verified.text
    second_cookie = verified.cookies.get("dms_session")

    codes = _race(
        engine,
        monkeypatch,
        owner=auth_sessions,
        attribute="revoke_all_for_user",
        first=lambda: client.delete("/v1/auth/mfa/totp", headers=cookie_headers(second_cookie)),
        second=lambda: _staff_session_revoke(client, dealership, anna),
    )

    assert codes == {"first": 204, "second": 204}
    db_session.expire_all()
    live = [
        row
        for row in db_session.scalars(select(AuthSession).where(AuthSession.user_id == uuid.UUID(anna["id"]))).all()
        if row.revoked_at is None
    ]
    assert live == []


# --- P1: a reset request vs an e-mail change ------------------------------------------------


def test_a_reset_request_racing_an_email_change_is_no_deadlock_and_mails_nobody(engine, client, mailbox, monkeypatch):
    """A first request left a live reset token. The e-mail change holds the
    user row and is paused before it ends the tokens; a second request for
    the old address starts. It used to end the live token and then wait for
    the user row to insert its own, while the change waited for that token.
    Now it waits for the user row first, then finds the address changed and
    answers like an unknown one (202, no reset mail)."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership, isDealerManager=False)
    PasswordLogin(client, mailbox).accept_invitation(anna)
    assert client.post("/v1/auth/password-reset/request", json={"email": anna["email"]}).status_code == 202
    version = client.get(f"/v1/dealerships/{dealership}/users/{anna['id']}", headers=platform_admin_headers()).json()[
        "version"
    ]
    mails_before = len(mailbox)

    codes = _race(
        engine,
        monkeypatch,
        owner=auth_tokens,
        attribute="consume_all_for_user",
        first=lambda: client.patch(
            f"/v1/dealerships/{dealership}/users/{anna['id']}",
            json={"email": "anna.neu@example.ch"},
            headers={**platform_admin_headers(), "If-Match": str(version)},
        ),
        second=lambda: client.post("/v1/auth/password-reset/request", json={"email": anna["email"]}),
    )

    assert codes == {"first": 200, "second": 202}
    # The change's own notice goes to the old address; no reset link does.
    assert [mail for mail in mailbox[mails_before:] if "password-reset" in mail.text] == []


# --- a recovery code spent while staff's reset deletes it ----------------------------------


def test_a_recovery_code_spent_while_staffs_reset_deletes_it_is_refused_never_a_500(engine, client, mailbox, monkeypatch):
    """Staff's reset is paused after it deleted the codes, uncommitted; the
    MFA step spends one. Its UPDATE waited for the row, then found it gone —
    a 0-row update, a 500. Now the step waits for the user row the reset
    holds, then finds no code: the refusal every wrong code gets."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login = PasswordLogin(client, mailbox)
    cookie = login.session_cookie(anna)
    _secret, recovery_codes = _enrol_totp(client, cookie)
    step = login.login(anna)
    assert step.json()["mfaRequired"] is True, step.text

    codes = _race(
        engine,
        monkeypatch,
        owner=mfa,
        attribute="remove_all_factors",
        after=True,
        first=lambda: _staff_mfa_reset(client, dealership, anna),
        second=lambda: client.post(
            "/v1/auth/mfa/verify", json={"challengeId": step.json()["challengeId"], "code": recovery_codes[0]}
        ),
    )

    assert codes["first"] == 204
    assert codes["second"] == 401


# --- every account and staff writer of a user-owned auth row locks the user first ----------


@pytest.mark.parametrize(
    "route",
    [
        "totp_enrol",
        "totp_confirm",
        "totp_remove",
        "recovery_codes_regenerate",
        "passkey_register_options",
        "passkey_register",
        "passkey_remove",
    ],
)
def test_every_account_mfa_write_takes_the_user_row_lock_first(route):
    source = inspect.getsource(getattr(auth_api, route))
    assert "get_own_user_for_update_or_404(" in source
    assert "get_own_user_or_404(" not in source


@pytest.mark.parametrize("route", ["revoke_user_sessions", "remove_user_mfa"])
def test_staffs_session_and_mfa_resets_take_the_user_row_lock_first(route):
    source = inspect.getsource(getattr(dealerships_api, route))
    assert "get_user_for_update_or_404(" in source
    assert "get_user_or_404(" not in source
