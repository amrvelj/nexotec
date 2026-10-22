"""KAN-196 — invitations, password resets, the password policy and the
platform sender (FR-AU-04…06, FR-AU-13…16, D-A-28): single-use hashed
tokens, uniform 202 on a reset request, the policy's three rules and the
breach check that fails open and flags, a delivery failure that is recorded
rather than swallowed.
"""

import datetime as dt
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.platform.models.auth_event import AuthEvent, AuthEventKind
from app.platform.models.auth_token import AuthToken, AuthTokenKind
from app.platform.models.credential import Credential
from app.platform.models.user import User, UserStatus
from app.platform.services import auth_tokens, mail_texts, password_policy
from app.platform.services.mailer import MailDeliveryError, get_mailer
from tests.conftest import fastapi_app
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
    token_in,
)


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _events(db_session, kind: AuthEventKind) -> list[AuthEvent]:
    return list(db_session.scalars(select(AuthEvent).where(AuthEvent.kind == kind)).all())


# --- invitations ---------------------------------------------------------------------


def test_creating_a_user_mails_an_invitation_that_sets_the_first_password_and_activates(client, mailbox, db_session):
    dealership = create_dealership(client, legalName="Garage Nord AG")
    anna = create_user(client, dealership)
    assert anna["status"] == "invited"

    mail = last_mail_to(mailbox, anna["email"])
    assert mail.subject == mail_texts.invitation(language=None, first_name="", dealership="", link="").subject
    assert "Garage Nord AG" in mail.text
    assert f"{get_settings().app_base_url}/invitations/accept?token=" in mail.text
    raw = token_in(mail)
    # Stored hashed, never in clear.
    stored = db_session.scalars(select(AuthToken)).all()
    assert [t.kind for t in stored] == [AuthTokenKind.INVITATION]
    assert stored[0].token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in stored[0].token_hash

    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD})
    assert response.status_code == 200, response.text
    assert "dms_session=" in response.headers.get("set-cookie", "")
    assert response.json()["mfaRequired"] is False
    assert response.json()["session"]["user"]["status"] == "active"
    db_session.expire_all()
    assert db_session.get(User, uuid.UUID(anna["id"])).status == UserStatus.ACTIVE
    assert len(_events(db_session, AuthEventKind.INVITATION_ACCEPTED)) == 1
    assert len(_events(db_session, AuthEventKind.PASSWORD_SET)) == 1

    # Single use.
    again = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": "another-strong-one-2026"})
    assert again.status_code == 400, again.text
    assert "no longer valid" in again.json()["error"]["message"]
    assert again.json()["error"]["details"] == {"rule": "link"}


def test_a_reissued_invitation_consumes_the_previous_one(client, mailbox):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    first = token_in(last_mail_to(mailbox, anna["email"]))

    response = client.post(
        f"/v1/dealerships/{dealership}/users/{anna['id']}/invitations", headers=platform_admin_headers()
    )
    assert response.status_code == 202, response.text
    second = token_in(last_mail_to(mailbox, anna["email"]))
    assert second != first

    assert (
        client.post("/v1/auth/invitations/accept", json={"token": first, "password": STRONG_PASSWORD}).status_code
        == 400
    )
    assert (
        client.post("/v1/auth/invitations/accept", json={"token": second, "password": STRONG_PASSWORD}).status_code
        == 200
    )


def test_an_expired_invitation_is_refused(client, mailbox, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    token = db_session.scalars(select(AuthToken)).one()
    token.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    db_session.commit()

    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD})
    assert response.status_code == 400


def test_no_invitation_goes_to_a_deactivated_user(client, mailbox, login):
    dealership = create_dealership(client)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    response = deactivate(client, dealership, beat, if_match=2)
    assert response.status_code == 200, response.text
    sent_before = len(mailbox)

    response = client.post(
        f"/v1/dealerships/{dealership}/users/{beat['id']}/invitations", headers=platform_admin_headers()
    )
    assert response.status_code == 409, response.text
    assert len(mailbox) == sent_before


# --- resets --------------------------------------------------------------------------


def test_a_reset_request_answers_202_either_way_and_mails_only_a_live_account(client, mailbox, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    client.patch(
        f"/v1/dealerships/{dealership}/users/{beat['id']}",
        json={"status": "suspended"},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    sent_before = len(mailbox)

    assert client.post("/v1/auth/password-reset/request", json={"email": "nobody@example.ch"}).status_code == 202
    assert client.post("/v1/auth/password-reset/request", json={"email": beat["email"]}).status_code == 202
    assert len(mailbox) == sent_before
    assert client.post("/v1/auth/password-reset/request", json={"email": anna["email"].upper()}).status_code == 202
    assert len(mailbox) == sent_before + 1
    assert mailbox[-1].to == anna["email"]
    requested = _events(db_session, AuthEventKind.PASSWORD_RESET_REQUESTED)
    assert sorted(bool(e.details["matched"]) for e in requested) == [False, False, True]


def test_a_reset_sets_the_password_ends_every_session_and_is_single_use(client, mailbox, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).status_code == 200

    client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    new_password = "a-brand-new-password-2026"
    response = client.post("/v1/auth/password-reset/confirm", json={"token": raw, "password": new_password})
    assert response.status_code == 204, response.text

    assert client.get("/v1/auth/me", headers=cookie_headers(cookie)).status_code == 401
    assert login.login(anna, STRONG_PASSWORD).status_code == 401
    assert login.login(anna, new_password).status_code == 200
    again = client.post("/v1/auth/password-reset/confirm", json={"token": raw, "password": "yet-another-one-2026"})
    assert again.status_code == 400
    assert len(_events(db_session, AuthEventKind.PASSWORD_RESET)) == 1
    revoked = _events(db_session, AuthEventKind.SESSION_REVOKED)
    assert [e.details for e in revoked if e.details["reason"] == "password_reset"] == [{"count": 1, "reason": "password_reset"}]
    assert again.json()["error"]["details"] == {"rule": "link"}


def test_the_mail_is_written_in_the_users_ui_language(client, mailbox, login):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    cookie = login.session_cookie(anna)
    response = client.put(
        "/v1/me/preferences/ui",
        json={"schemaVersion": 1, "uiLanguage": "fr"},
        headers=cookie_headers(cookie),
    )
    assert response.status_code in (200, 201), response.text

    client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})

    mail = last_mail_to(mailbox, anna["email"])
    assert mail.subject == mail_texts.password_reset(language="fr", first_name="", link="").subject
    assert mail.subject != mail_texts.password_reset(language="de", first_name="", link="").subject


# --- the password policy -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "rule"),
    [
        ("Short-pw-1!", "min_length"),
        ("anna-is-my-first-name-2026", "deny_list"),
        ("Musterbetrieb-forever-2026", "deny_list"),
        ("x" * 11 + "muster", "deny_list"),
    ],
)
def test_the_policy_refuses_short_passwords_and_names(client, mailbox, password, rule):
    dealership = create_dealership(client)  # "Garage Musterbetrieb AG"
    anna = create_user(client, dealership)
    raw = token_in(last_mail_to(mailbox, anna["email"]))

    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": password})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["rule"] == rule
    assert password not in response.text
    # A refused password leaves the invitation usable.
    assert (
        client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD}).status_code == 200
    )


def test_the_last_five_passwords_are_refused(client, mailbox, login, db_session):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)

    def reset_to(password: str):
        client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})
        raw = token_in(last_mail_to(mailbox, anna["email"]))
        return client.post("/v1/auth/password-reset/confirm", json={"token": raw, "password": password})

    refused = reset_to(STRONG_PASSWORD)
    assert refused.status_code == 400
    assert refused.json()["error"]["details"] == {"rule": "reused"}
    for i in range(4):
        assert reset_to(f"rotation-number-{i}-of-the-year-2026").status_code == 204, i
    # Five hashes are remembered: the original is still refused …
    assert reset_to(STRONG_PASSWORD).status_code == 400
    assert reset_to("rotation-number-4-of-the-year-2026").status_code == 204
    # … until it falls out of the window of five.
    assert reset_to(STRONG_PASSWORD).status_code == 204
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert len(credential.password_history) == 4


def test_a_breached_password_is_refused_and_an_unavailable_check_is_flagged(client, mailbox, db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "hibp_enabled", True)
    asked: list[str] = []

    def breached(password: str, *, timeout: float) -> int | None:
        asked.append(password)
        return 12345

    monkeypatch.setattr(password_policy, "hibp_breach_count", breached)
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    raw = token_in(last_mail_to(mailbox, anna["email"]))

    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["rule"] == "breached"
    assert asked == [STRONG_PASSWORD]

    # FR-AU-05: the service does not answer — the set succeeds and is flagged.
    monkeypatch.setattr(password_policy, "hibp_breach_count", lambda password, *, timeout: None)
    response = client.post("/v1/auth/invitations/accept", json={"token": raw, "password": STRONG_PASSWORD})
    assert response.status_code == 200, response.text
    credential = db_session.scalar(select(Credential).where(Credential.user_id == anna["id"]))
    assert credential.breach_check_pending is True
    assert len(_events(db_session, AuthEventKind.BREACH_CHECK_UNAVAILABLE)) == 1


def test_the_breach_check_sends_five_hash_characters_never_the_password(monkeypatch):
    seen: dict = {}

    digest = hashlib.sha1(b"password").hexdigest().upper()

    class _Response:
        status_code = 200
        text = f"0018A45C4D1DEF81644B54AB7F969B88D65:3\n{digest[5:]}:1\n"

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        return _Response()

    monkeypatch.setattr(password_policy.httpx, "get", fake_get)
    count = password_policy.hibp_breach_count("password", timeout=1.0)
    assert seen["url"].endswith("/range/" + digest[:5])
    assert digest[5:] not in seen["url"]
    assert seen["headers"]["Add-Padding"] == "true"
    assert count == 1


# --- the platform sender -------------------------------------------------------------------


def test_a_delivery_failure_is_recorded_and_the_user_still_exists(client, db_session):
    class _Broken:
        def send(self, mail):
            raise MailDeliveryError("connection refused")

    fastapi_app.dependency_overrides[get_mailer] = lambda: _Broken()
    try:
        dealership = create_dealership(client)
        anna = create_user(client, dealership)
    finally:
        fastapi_app.dependency_overrides.pop(get_mailer, None)

    failed = _events(db_session, AuthEventKind.MAIL_DELIVERY_FAILED)
    assert [str(e.user_id) for e in failed] == [anna["id"]]
    assert len(_events(db_session, AuthEventKind.INVITATION_ISSUED)) == 1
    # The token exists; a manager re-issues once the mailbox is back.
    assert db_session.scalar(select(AuthToken).where(AuthToken.user_id == uuid.UUID(anna["id"]))) is not None


def test_a_reset_activates_an_invited_user_who_never_accepted(client, mailbox, db_session):
    """The reset link reaches the same mailbox the invitation did: a user who
    sets a password this way is active, never a working login in `invited`."""

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})
    raw = token_in(last_mail_to(mailbox, anna["email"]))
    assert client.post("/v1/auth/password-reset/confirm", json={"token": raw, "password": STRONG_PASSWORD}).status_code == 204

    response = client.post("/v1/auth/login", json={"email": anna["email"], "password": STRONG_PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["session"]["user"]["status"] == "active"


def test_the_reset_mail_leaves_after_the_response_and_a_failure_is_still_recorded(client, mailbox, login, db_session):
    """FR-AU-16: the request itself sends nothing — the mail is a background
    task, so a slow mailbox cannot say whether the address exists. The
    background half records a delivery failure in a session of its own."""

    from app.platform.services import identity

    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    sent_before = len(mailbox)

    pending = identity.request_password_reset(db_session, email=anna["email"], ip_address="203.0.113.9")
    db_session.commit()
    assert pending is not None and pending.mail.to == anna["email"]
    assert len(mailbox) == sent_before  # nothing sent inside the request
    assert identity.request_password_reset(db_session, email="nobody@example.ch", ip_address="203.0.113.9") is None

    class _Broken:
        def send(self, mail):
            raise MailDeliveryError("connection refused")

    identity.send_pending_mail(_Broken(), pending, bind=db_session.get_bind(), ip_address="203.0.113.9")
    failed = _events(db_session, AuthEventKind.MAIL_DELIVERY_FAILED)
    assert [str(e.user_id) for e in failed] == [anna["id"]]
    assert failed[0].ip_address == "203.0.113.9"
    identity.send_pending_mail(_Broken(), None, bind=db_session.get_bind(), ip_address="")  # nothing to send


def test_the_logging_sender_is_refused_in_production(monkeypatch):
    from app.platform.services import mailer

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "platform_mail_sender", "logging")
    monkeypatch.setattr(mailer, "_mailer", None)
    with pytest.raises(RuntimeError, match="smtp"):
        mailer.get_mailer()
    monkeypatch.setattr(settings, "platform_mail_sender", "smtp")
    assert isinstance(mailer.get_mailer(), mailer.SmtpMailer)


def test_changing_the_address_ends_tokens_and_sessions_and_tells_the_old_address(client, login, mailbox, db_session):
    """The address is the sign-in and recovery channel, so a change is a
    credential change: an invitation or reset mailed to the old address is
    dead, every session ends, the event names the actor, the old address
    hears of it."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = login.session_cookie(manager)
    beat = create_user(client, dealership, isDealerManager=False)
    beats = login.session_cookie(beat)
    client.post("/v1/auth/password-reset/request", json={"email": beat["email"]})
    reset_raw = token_in(last_mail_to(mailbox, beat["email"]))
    old_email = beat["email"]

    # D-A-16: once the account is active, changing its address is recovery —
    # platform staff's (with a factor in the session), never the manager's;
    # since KAN-202 at the recovery door with a reason, never the PATCH.
    for headers in (cookie_headers(managers), platform_admin_headers()):
        refused = client.patch(
            f"/v1/dealerships/{dealership}/users/{beat['id']}",
            json={"email": "Beat.Neu@example.ch"},
            headers={**headers, "If-Match": "2"},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["details"] == {"recovery": "change-email"}
    staff_id = uuid.uuid4()
    response = change_email(client, beat["id"], "Beat.Neu@example.ch", if_match=2, headers=platform_admin_headers(user_id=staff_id))
    assert response.status_code == 200, response.text
    assert response.json()["userVersion"] == 3
    assert client.get(f"/v1/dealerships/{dealership}/users/{beat['id']}", headers=platform_admin_headers()).json()["email"] == "beat.neu@example.ch"

    assert client.get("/v1/auth/me", headers=cookie_headers(beats)).status_code == 401
    dead = client.post("/v1/auth/password-reset/confirm", json={"token": reset_raw, "password": "a-brand-new-password-2026"})
    assert dead.status_code == 400
    notice = last_mail_to(mailbox, old_email)
    assert notice.subject == mail_texts.email_changed(language=None, first_name="", new_email="").subject
    assert "beat.neu@example.ch" in notice.text
    (event,) = _events(db_session, AuthEventKind.EMAIL_CHANGED)
    assert event.actor_id == staff_id
    assert event.details == {"from": old_email, "to": "beat.neu@example.ch"}
    # The password did not change: the user signs in again at the new address.
    assert client.post("/v1/auth/login", json={"email": "beat.neu@example.ch", "password": STRONG_PASSWORD}).status_code == 200

    # An invitation mailed to the old address dies the same way — and while
    # the user never accepted it, correcting the address is the manager's.
    dora = create_user(client, dealership, isDealerManager=False, firstName="Dora")
    invite_raw = token_in(last_mail_to(mailbox, dora["email"]))
    corrected = client.patch(
        f"/v1/dealerships/{dealership}/users/{dora['id']}",
        json={"email": "dora.richtig@example.ch"},
        headers={**cookie_headers(managers), "If-Match": "1"},
    )
    assert corrected.status_code == 200, corrected.text
    assert client.post("/v1/auth/invitations/accept", json={"token": invite_raw, "password": STRONG_PASSWORD}).status_code == 400


def test_an_address_already_in_use_is_a_409_not_a_500(client, login, mailbox):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    managers = login.session_cookie(manager)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    beat = create_user(client, dealership, isDealerManager=False)

    taken = client.patch(
        f"/v1/dealerships/{dealership}/users/{beat['id']}",
        json={"email": anna["email"].upper()},
        headers={**cookie_headers(managers), "If-Match": "1"},
    )
    assert taken.status_code == 409, taken.text
    taken = change_email(client, anna["id"], beat["email"], if_match=2)
    assert taken.status_code == 409, taken.text
    assert taken.json()["error"]["details"] == {"rule": "email_taken"}


def test_staff_move_an_active_users_address_only_with_a_factor_in_the_session(client, login):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    login.accept_invitation(anna)
    path = f"/v1/dealerships/{dealership}/users/{anna['id']}"

    refused = change_email(client, anna["id"], "anna.neu@example.ch", if_match=2, headers=platform_admin_headers(mfa=False))
    assert refused.status_code == 403, refused.text
    assert "second factor" in refused.json()["error"]["message"]
    # Other fields are not recovery: the same session edits them.
    assert client.patch(path, json={"phone": "+41791234567"}, headers={**platform_admin_headers(mfa=False), "If-Match": "2"}).status_code == 200
    assert change_email(client, anna["id"], "anna.neu@example.ch", if_match=3).status_code == 200


def test_one_recovery_path_succeeding_closes_the_other(client, login, mailbox):
    dealership = create_dealership(client)
    anna = create_user(client, dealership)
    invite_raw = token_in(last_mail_to(mailbox, anna["email"]))
    client.post("/v1/auth/password-reset/request", json={"email": anna["email"]})
    reset_raw = token_in(last_mail_to(mailbox, anna["email"]))

    assert client.post("/v1/auth/password-reset/confirm", json={"token": reset_raw, "password": STRONG_PASSWORD}).status_code == 204
    assert client.post("/v1/auth/invitations/accept", json={"token": invite_raw, "password": "another-strong-one-2026"}).status_code == 400

    beat = create_user(client, dealership, isDealerManager=False)
    invite_raw = token_in(last_mail_to(mailbox, beat["email"]))
    client.post("/v1/auth/password-reset/request", json={"email": beat["email"]})
    reset_raw = token_in(last_mail_to(mailbox, beat["email"]))
    assert client.post("/v1/auth/invitations/accept", json={"token": invite_raw, "password": STRONG_PASSWORD}).status_code == 200
    assert client.post("/v1/auth/password-reset/confirm", json={"token": reset_raw, "password": "another-strong-one-2026"}).status_code == 400


def test_the_stored_address_is_lower_case_by_constraint(client, db_session):
    """`ck_user_email_lower`: the unique index on `user.email` cannot admit a
    second spelling of one mailbox once every row is lower-cased."""

    from sqlalchemy.exc import IntegrityError

    from app.core.uuid7 import uuid7
    from app.platform.models.user import UserRole

    dealership = create_dealership(client)
    db_session.add(
        User(
            id=uuid7(),
            tenant_id=uuid.UUID(dealership),
            first_name="Max",
            last_name="Muster",
            email="Max.Muster@Example.CH",
            role=UserRole.SALES,
            access_roles=["sales"],
            auth_identity_id="local:mixed-case",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- tokens --------------------------------------------------------------------------------


def test_a_token_is_consumed_exactly_once(client, db_session):
    user_id = uuid.UUID(create_user(client, create_dealership(client))["id"])
    raw = auth_tokens.issue(db_session, user_id=user_id, kind=AuthTokenKind.PASSWORD_RESET, actor_id=None)
    db_session.commit()

    assert auth_tokens.consume(db_session, raw=raw, kind=AuthTokenKind.INVITATION) is None, "another kind"
    first = auth_tokens.consume(db_session, raw=raw, kind=AuthTokenKind.PASSWORD_RESET)
    db_session.commit()
    assert first is not None
    assert auth_tokens.consume(db_session, raw=raw, kind=AuthTokenKind.PASSWORD_RESET) is None
    assert auth_tokens.peek(db_session, raw=raw, kind=AuthTokenKind.PASSWORD_RESET) is None


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-level UPDATE race: Postgres only")
def test_two_concurrent_presentations_of_one_token_cannot_both_win(client, engine):
    user_id = uuid.UUID(create_user(client, create_dealership(client))["id"])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    setup = factory()
    raw = auth_tokens.issue(setup, user_id=user_id, kind=AuthTokenKind.INVITATION, actor_id=None)
    setup.commit()
    setup.close()

    def consume() -> bool:
        session = factory()
        try:
            token = auth_tokens.consume(session, raw=raw, kind=AuthTokenKind.INVITATION)
            session.commit()
            return token is not None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: consume(), range(4)))
    assert outcomes.count(True) == 1, outcomes
