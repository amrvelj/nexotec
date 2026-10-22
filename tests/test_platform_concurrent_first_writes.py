"""KAN-393 and KAN-400: platform writes that raced each other into a 500.

KAN-393 — four more first writes read the row, found none and inserted: two
at once, and the second insert hit the primary key or unique constraint at
an unguarded flush. The auth-config PUT, the first TOTP enrolment and the
first preference save now insert in a savepoint and carry on with the
winner's committed row; the first password (`credential`) is written under
the user-row lock the front door now takes.

KAN-400 — invitation acceptance (and the reset confirm) consumed its token
and then wrote the user row unlocked, while a manager's e-mail change holds
the user row and then ends the user's tokens: opposite lock orders, a
deadlock. And an unlocked acceptance racing an admin PATCH wrote
`version = <read> + 1`, losing a bump. The front door now locks the user
row before it consumes the token.

Each race is staged the same way: a session of the test's own holds the
competing write uncommitted, the request under test starts and must wait on
a lock (asserted through pg_locks), then the held write commits.
"""

import datetime as dt
import threading
import uuid

import pyotp
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import utcnow
from app.platform.models.auth_token import AuthTokenKind
from app.platform.models.credential import Credential
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.mfa import MfaTotp
from app.platform.models.user import User, UserStatus
from app.platform.models.user_preference import UserPreference
from app.platform.services import auth_tokens, identity, mfa
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    last_mail_to,
    platform_admin_headers,
    recover,
    token_in,
)
from tests.test_customer_vehicle import _create_dealer
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock
from tests.test_identity_mfa import _enrol_totp


def _held(engine) -> Session:
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _while_held(engine, held: Session, send):
    """Starts `send` on a thread, waits until it blocks on the held session's
    lock, commits the held session and returns the response."""

    errors: list[Exception] = []
    responses: list = []
    thread = _run("request", errors, lambda: responses.append(send()))
    try:
        _wait_until_a_backend_waits_on_a_lock(engine)
        held.commit()
    finally:
        held.close()
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert errors == []
    (response,) = responses
    return response


# --- KAN-393: auth-config -------------------------------------------------------------------


def test_a_first_auth_config_put_racing_another_is_a_409_on_the_winners_version(engine, client, db_session):
    _postgres_only(engine)
    dealership = _create_dealer(client)
    held = _held(engine)
    held.add(DealershipAuthConfig(dealership_id=uuid.UUID(dealership), mfa_enforced=False, version=2))
    held.flush()

    response = _while_held(
        engine,
        held,
        lambda: client.put(
            f"/v1/dealerships/{dealership}/auth-config",
            json={"mfaEnforced": False},
            headers={**platform_admin_headers(), "If-Match": "1"},
        ),
    )

    assert response.status_code == 409, response.text
    rows = db_session.scalars(
        select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == uuid.UUID(dealership))
    ).all()
    assert [(r.mfa_enforced, r.version) for r in rows] == [(False, 2)]


def test_a_first_auth_config_put_racing_an_unchanged_first_write_goes_on_from_its_row(engine, client, db_session):
    _postgres_only(engine)
    dealership = _create_dealer(client)
    held = _held(engine)
    held.add(DealershipAuthConfig(dealership_id=uuid.UUID(dealership)))
    held.flush()

    response = _while_held(
        engine,
        held,
        lambda: client.put(
            f"/v1/dealerships/{dealership}/auth-config",
            json={"mfaEnforced": False},
            headers={**platform_admin_headers(), "If-Match": "1"},
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"authMethod": "local", "mfaEnforced": False, "version": 2}


# --- KAN-393: first TOTP enrolment ---------------------------------------------------------


def test_a_first_totp_enrolment_racing_another_replaces_its_secret(engine, client, mailbox, db_session):
    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)
    held = _held(engine)
    held.add(MfaTotp(user_id=uuid.UUID(anna["id"]), secret=pyotp.random_base32()))
    held.flush()

    response = _while_held(engine, held, lambda: client.post("/v1/auth/mfa/totp/enrol", headers=cookie_headers(cookie)))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    (row,) = db_session.scalars(select(MfaTotp).where(MfaTotp.user_id == uuid.UUID(anna["id"]))).all()
    assert row.secret == response.json()["secret"]  # last one wins, as on a re-enrol
    assert row.confirmed_at is None


# --- KAN-393: first preference save --------------------------------------------------------


def test_a_first_preference_save_racing_another_overwrites_it(engine, client, mailbox, db_session):
    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)
    held = _held(engine)
    held.add(UserPreference(user_id=uuid.UUID(anna["id"]), scope="grid.customers", payload={"density": "compact"}))
    held.flush()

    response = _while_held(
        engine,
        held,
        lambda: client.put(
            "/v1/me/preferences/grid.customers",
            json={"schemaVersion": 1, "density": "comfortable"},
            headers=cookie_headers(cookie),
        ),
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    (row,) = db_session.scalars(select(UserPreference).where(UserPreference.user_id == uuid.UUID(anna["id"]))).all()
    assert row.payload["density"] == "comfortable"


# --- KAN-393: first password, KAN-400: one lock order at the front door --------------------


def _lock_user_row(held: Session, user_id: str) -> None:
    held.execute(text('SELECT id FROM "user" WHERE id = :id FOR NO KEY UPDATE'), {"id": uuid.UUID(user_id)})


def test_an_acceptance_racing_another_first_password_write_waits_for_it(engine, client, mailbox, db_session):
    """Another front-door write of the same user (here: its first credential,
    under the user-row lock as every front-door write now takes it) is in
    flight. The acceptance waits for the lock, then finds the credential and
    replaces its password — it used to insert a second one and 500."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    held = _held(engine)
    _lock_user_row(held, anna["id"])
    held.add(Credential(user_id=uuid.UUID(anna["id"]), password_hash="held-by-the-other-write"))
    held.flush()

    response = _while_held(
        engine,
        held,
        lambda: client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD}),
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    (credential,) = db_session.scalars(select(Credential).where(Credential.user_id == uuid.UUID(anna["id"]))).all()
    assert credential.password_hash != "held-by-the-other-write"
    assert credential.password_history == ["held-by-the-other-write"]


def test_an_acceptance_racing_an_admin_patch_keeps_both_version_bumps(engine, client, mailbox, db_session):
    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    held = _held(engine)
    _lock_user_row(held, anna["id"])
    held.execute(text('UPDATE "user" SET version = version + 1 WHERE id = :id'), {"id": uuid.UUID(anna["id"])})

    response = _while_held(
        engine,
        held,
        lambda: client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD}),
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    user = db_session.get(User, uuid.UUID(anna["id"]))
    assert user.version == anna["version"] + 2


def test_an_acceptance_racing_an_email_change_is_refused_never_a_deadlock(engine, client, mailbox, db_session, monkeypatch):
    """The PATCH holds the user row (KAN-380) and is paused just before it
    ends the user's tokens; the acceptance starts. It used to consume its
    token first and then wait for the user row, while the PATCH waited for
    the token: a deadlock, one of them a 500. Now the acceptance waits for
    the user row first; the PATCH ends the invitation and commits; the
    acceptance finds its link dead (400)."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership, isDealerManager=False)
    raw = token_in(last_mail_to(mailbox, anna["email"]))

    reached, release = threading.Event(), threading.Event()
    claimed = threading.Lock()
    real_consume_all = auth_tokens.consume_all_for_user

    def _consume_all(db, **kwargs):
        if claimed.acquire(blocking=False):
            reached.set()
            release.wait(timeout=10)
        return real_consume_all(db, **kwargs)

    monkeypatch.setattr(auth_tokens, "consume_all_for_user", _consume_all)

    errors: list[Exception] = []
    codes: dict[str, int] = {}

    def _patch():
        codes["patch"] = client.patch(
            f"/v1/dealerships/{dealership}/users/{anna['id']}",
            json={"email": "anna.richtig@example.ch"},
            headers={**platform_admin_headers(), "If-Match": str(anna["version"])},
        ).status_code

    def _accept():
        codes["accept"] = client.post(
            "/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD}
        ).status_code

    patch = _run("email-change", errors, _patch)
    try:
        assert reached.wait(timeout=10), "the e-mail change never reached its token step"
        accept = _run("acceptance", errors, _accept)
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        patch.join(timeout=15)
        accept.join(timeout=15)
    finally:
        release.set()
    assert not patch.is_alive() and not accept.is_alive()
    assert errors == []
    assert codes == {"patch": 200, "accept": 400}
    db_session.expire_all()
    user = db_session.get(User, uuid.UUID(anna["id"]))
    assert (user.email, user.status.value) == ("anna.richtig@example.ch", "invited")


# --- KAN-200: a login takes the user row before the credential ----------------------------


def _password_login(client, user: dict, password: str = STRONG_PASSWORD):
    return client.post("/v1/auth/login", json={"email": user["email"], "password": password})


def test_a_login_racing_a_reset_confirm_waits_for_it_and_meets_the_new_password(
    engine, client, mailbox, db_session, monkeypatch
):
    """KAN-200 made a completed login write the user row (`last_login_at`);
    it wrote the credential first, while the reset confirm (KAN-400) holds
    the user row and then writes the credential: a deadlock, the login a
    500. The login now locks the user row first, so it waits for the reset,
    and the password the reset replaced no longer signs in."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    PasswordLogin(client, mailbox).accept_invitation(anna)
    # One typo: the credential carries a failure counter a login clears.
    assert _password_login(client, anna, "wrong-wrong-wrong-1").status_code == 401
    assert client.post("/v1/auth/password-reset/request", json={"email": anna["email"]}).status_code == 202
    raw = token_in(last_mail_to(mailbox, anna["email"]))

    reached, release = threading.Event(), threading.Event()
    real_set_password = identity.set_password

    def _paused_set_password(db, **kwargs):
        reached.set()  # the reset holds the user row here
        release.wait(timeout=15)
        return real_set_password(db, **kwargs)

    monkeypatch.setattr(identity, "set_password", _paused_set_password)
    errors: list[Exception] = []
    codes: dict[str, int] = {}

    def _reset():
        codes["reset"] = client.post(
            "/v1/auth/password-reset/confirm", json={"token": raw, "password": "another-strong-passphrase-2026"}
        ).status_code

    def _login():
        codes["login"] = _password_login(client, anna).status_code

    reset = _run("reset", errors, _reset)
    login = None
    try:
        assert reached.wait(timeout=10)
        login = _run("login", errors, _login)
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        reset.join(timeout=20)
        login.join(timeout=20)
    finally:
        release.set()
    assert login is not None and not login.is_alive() and not reset.is_alive()
    assert errors == []
    assert codes == {"reset": 204, "login": 401}
    assert _password_login(client, anna, "another-strong-passphrase-2026").status_code == 200


def test_a_login_racing_a_deactivation_is_judged_by_the_committed_status(engine, client, mailbox, db_session):
    """The password step reads the user, then waits for the row a
    deactivation holds; under the lock it reads `deactivated` and refuses —
    it never mints a session after the deactivation revoked them all."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    PasswordLogin(client, mailbox).accept_invitation(anna)
    held = _held(engine)
    held.execute(
        text("SELECT id FROM \"user\" WHERE id = :id FOR NO KEY UPDATE"), {"id": uuid.UUID(anna["id"])}
    )
    held.get(User, uuid.UUID(anna["id"])).status = UserStatus.DEACTIVATED
    held.flush()

    response = _while_held(engine, held, lambda: _password_login(client, anna))

    assert response.status_code == 401, response.text


def test_the_mfa_step_racing_a_deactivation_is_judged_by_the_committed_status(engine, client, mailbox, db_session):
    """The second step reads the user from its challenge; the lock it takes
    before minting (`_finish_login`) waits for the deactivation, and the
    session is refused."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)
    _secret, recovery_codes = _enrol_totp(client, cookie)
    first = _password_login(client, anna)
    assert first.status_code == 200 and first.json()["mfaRequired"] is True, first.text
    held = _held(engine)
    held.execute(
        text("SELECT id FROM \"user\" WHERE id = :id FOR NO KEY UPDATE"), {"id": uuid.UUID(anna["id"])}
    )
    held.get(User, uuid.UUID(anna["id"])).status = UserStatus.DEACTIVATED
    held.flush()

    response = _while_held(
        engine,
        held,
        lambda: client.post(
            "/v1/auth/mfa/verify", json={"challengeId": first.json()["challengeId"], "code": recovery_codes[0]}
        ),
    )

    assert response.status_code == 401, response.text


@pytest.mark.parametrize("competitor", ["password_login", "invitation_accept"])
def test_the_mfa_step_takes_the_user_row_before_its_challenge(engine, client, mailbox, monkeypatch, competitor):
    """The password step and the acceptance of a re-issued invitation hold
    the user row while they issue a challenge (which ends the user's live
    ones); the MFA step locked its challenge and its factor first and the
    user row only at the mint — a deadlock. It now takes the user row first,
    so the other request waits for it and both succeed."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = PasswordLogin(client, mailbox).session_cookie(anna)
    _secret, recovery_codes = _enrol_totp(client, cookie)
    raw_invitation = None
    if competitor == "invitation_accept":
        reissued = recover(client, anna["id"], "reissue-invitation")
        assert reissued.status_code == 200, reissued.text
        raw_invitation = token_in(last_mail_to(mailbox, anna["email"]))
    first = _password_login(client, anna)
    assert first.status_code == 200 and first.json()["mfaRequired"] is True, first.text

    reached, release = threading.Event(), threading.Event()
    real_use = mfa.use_recovery_code

    def _paused_use(db, **kwargs):
        used = real_use(db, **kwargs)
        reached.set()  # the step holds its challenge and the recovery code here
        release.wait(timeout=15)
        return used

    monkeypatch.setattr(mfa, "use_recovery_code", _paused_use)
    errors: list[Exception] = []
    codes: dict[str, int] = {}

    def _verify():
        codes["verify"] = client.post(
            "/v1/auth/mfa/verify", json={"challengeId": first.json()["challengeId"], "code": recovery_codes[0]}
        ).status_code

    def _competitor():
        if competitor == "password_login":
            codes[competitor] = _password_login(client, anna).status_code
        else:
            codes[competitor] = client.post(
                "/v1/auth/invitations/accept", json={"token": raw_invitation, "password": "a-brand-new-password-2026"}
            ).status_code

    verify = _run("verify", errors, _verify)
    other = None
    try:
        assert reached.wait(timeout=10)
        other = _run(competitor, errors, _competitor)
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        verify.join(timeout=20)
        other.join(timeout=20)
    finally:
        release.set()
    assert other is not None and not other.is_alive() and not verify.is_alive()
    assert errors == []
    assert codes == {"verify": 200, competitor: 200}


def test_a_re_sent_invitation_racing_the_acceptance_of_the_previous_one_ends_it(engine, client, mailbox, monkeypatch):
    """The re-send ends the live invitations and then stamps `invited_at`
    (KAN-200); the acceptance holds the user row before it consumes its
    token (KAN-400). Unlocked, the re-send deadlocked against it. It now
    takes the user row first: the acceptance waits, finds its link ended by
    the new invitation and says so."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    old_raw = token_in(last_mail_to(mailbox, anna["email"]))

    reached, release = threading.Event(), threading.Event()
    real_issue = auth_tokens.issue

    def _paused_issue(db, **kwargs):
        raw = real_issue(db, **kwargs)
        if kwargs.get("kind") == AuthTokenKind.INVITATION:
            reached.set()  # the re-send holds the user row and the old invitation here
            release.wait(timeout=15)
        return raw

    monkeypatch.setattr(auth_tokens, "issue", _paused_issue)
    errors: list[Exception] = []
    codes: dict[str, int] = {}

    def _resend():
        codes["resend"] = client.post(
            f"/v1/dealerships/{dealership}/users/{anna['id']}/invitations", headers=platform_admin_headers()
        ).status_code

    def _accept():
        codes["accept"] = client.post(
            "/v1/auth/invitations/accept", json={"token": old_raw, "password": "a-brand-new-password-2026"}
        ).status_code

    resend = _run("resend", errors, _resend)
    accept = None
    try:
        assert reached.wait(timeout=10)
        accept = _run("accept", errors, _accept)
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        resend.join(timeout=20)
        accept.join(timeout=20)
    finally:
        release.set()
    assert accept is not None and not accept.is_alive() and not resend.is_alive()
    assert errors == []
    assert codes == {"resend": 202, "accept": 400}


def test_a_login_queued_behind_the_lock_meets_the_lockout_written_meanwhile(engine, client, mailbox, db_session):
    """FR-AU-08 under a burst: an attempt that waited for the user row is
    judged against the lock an earlier attempt wrote while it waited — not
    against the credential it read before (a correct password is refused
    too, as on any locked account)."""

    _postgres_only(engine)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    PasswordLogin(client, mailbox).accept_invitation(anna)
    held = _held(engine)
    held.execute(text('SELECT id FROM "user" WHERE id = :id FOR NO KEY UPDATE'), {"id": uuid.UUID(anna["id"])})
    credential = held.scalar(select(Credential).where(Credential.user_id == uuid.UUID(anna["id"])))
    credential.failed_login_attempts = 5
    credential.locked_until = utcnow() + dt.timedelta(minutes=15)
    held.flush()

    response = _while_held(engine, held, lambda: _password_login(client, anna))

    assert response.status_code == 401, response.text
