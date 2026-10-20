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

import threading
import uuid

import pyotp
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.models.credential import Credential
from app.platform.models.dealership_auth_config import DealershipAuthConfig
from app.platform.models.mfa import MfaTotp
from app.platform.models.user import User
from app.platform.models.user_preference import UserPreference
from app.platform.services import auth_tokens
from tests.identity_support import (
    STRONG_PASSWORD,
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    last_mail_to,
    platform_admin_headers,
    token_in,
)
from tests.test_customer_vehicle import _create_dealer
from tests.test_customer_vehicle_party_close_rule import _postgres_only, _run, _wait_until_a_backend_waits_on_a_lock


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
