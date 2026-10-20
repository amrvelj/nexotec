"""The identity front door (KAN-196; Authentication & Identity D-A-07, D-A-08,
D-A-11, D-A-12, FR-AU-01…16).

`IdentityProvider` is the seam (FR-AU-01): one method, presented credentials
in, a resolved user out or a refusal. `LocalPasswordProvider` is the only
implementation enabled in v1; `EntraOidcProvider` (D-A-11) is [P3]. Whichever
provider authenticates, the session is minted identically by
app.platform.api.auth (A-RULE-AU-1).

What the local provider guarantees:

- Uniform refusal (FR-AU-07, FR-AU-16): unknown address, wrong password,
  locked, inactive — the same 401 body, and the same argon2 work (a dummy
  verify when there is no credential), so neither the response nor its
  timing says whether the account exists. tests/test_login_uniformity.py
  asserts both.
- Throttling on two axes (FR-AU-08): per account (failures on the
  credential) and per source IP (failures in the auth event stream),
  each with exponential backoff; soft lockout on the credential after
  `auth_lockout_failures` (FR-AU-09), cleared by a successful reset.
- Rehash on login when the stored parameters fall below policy.
- Nobody sets another person's password (FR-AU-10): a password is set only
  by its owner through an invitation or a reset token.
"""

import dataclasses
import datetime as dt
import logging
import uuid
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_token import AuthTokenKind
from app.platform.models.credential import Credential
from app.platform.models.dealership import Dealership
from app.platform.models.dealership_auth_config import AuthMethod, DealershipAuthConfig
from app.platform.models.user import User, UserStatus
from app.platform.services import auth_events, auth_sessions, auth_tokens, mail_texts, password_hashing
from app.platform.services import user_preference as preference_service
from app.platform.services.mailer import Mail, MailDeliveryError, PlatformMailer
from app.platform.services.password_policy import validate_new_password

logger = logging.getLogger(__name__)
_settings = get_settings()

INVALID_CREDENTIALS = "Invalid e-mail address or password."
_INACTIVE = frozenset({UserStatus.SUSPENDED, UserStatus.DEACTIVATED})
PASSWORD_HISTORY_DEPTH = 5


@dataclasses.dataclass(frozen=True)
class PresentedCredentials:
    email: str
    password: str
    ip_address: str


class IdentityProvider(Protocol):
    def resolve(self, db: Session, presented: PresentedCredentials) -> User: ...


# --- throttling and lockout ---------------------------------------------------------------


def _backoff(failures: int) -> dt.timedelta:
    """Exponential: nothing until the third failure, then 2, 4, 8 … seconds,
    capped at the lockout length."""

    if failures < 3:
        return dt.timedelta(0)
    seconds = min(2 ** (failures - 2), _settings.auth_lockout_minutes * 60)
    return dt.timedelta(seconds=seconds)


def _account_throttled(credential: Credential | None, now: dt.datetime) -> bool:
    if credential is None:
        return False
    if credential.locked_until is not None and credential.locked_until > now:
        return True
    if credential.last_failed_at is None:
        return False
    return credential.last_failed_at + _backoff(credential.failed_login_attempts) > now


def _ip_throttled(db: Session, ip_address: str, now: dt.datetime) -> bool:
    if not ip_address:
        return False
    count, latest = auth_events.recent_failures_from_ip(
        db, ip_address=ip_address, window=dt.timedelta(minutes=_settings.auth_ip_window_minutes)
    )
    if latest is None:
        return False
    return latest + _backoff(count) > now


def _credential_of(db: Session, user_id: uuid.UUID) -> Credential | None:
    return db.scalar(select(Credential).where(Credential.user_id == user_id))


def find_user_by_email(db: Session, email: str) -> User | None:
    """Case-insensitive: an address is typed in any case, and rows written
    before KAN-196 may keep the local part's case (new rows are stored
    lower-cased by the user service)."""

    normalised = email.strip().lower()
    return db.scalar(select(User).where(func.lower(User.email) == normalised).order_by(User.created_at).limit(1))


class LocalPasswordProvider:
    """FR-AU-01's live implementation: e-mail + argon2id password."""

    def resolve(self, db: Session, presented: PresentedCredentials) -> User:
        now = utcnow()
        user = find_user_by_email(db, presented.email)
        credential = _credential_of(db, user.id) if user is not None else None

        # Every refusal below records, commits and raises the same thing.
        def refuse(kind: AuthEventKind, details: dict | None = None) -> UnauthorizedError:
            auth_events.record(
                db,
                kind,
                user_id=user.id if user else None,
                dealership_id=user.tenant_id if user else None,
                ip_address=presented.ip_address,
                details=details,
            )
            db.commit()
            return UnauthorizedError(INVALID_CREDENTIALS)

        if _ip_throttled(db, presented.ip_address, now) or _account_throttled(credential, now):
            # The verify work still runs: a throttled answer must not be the
            # fast one either.
            password_hashing.burn_verify()
            raise refuse(AuthEventKind.LOGIN_THROTTLED)

        if user is None or credential is None:
            password_hashing.burn_verify()
            raise refuse(AuthEventKind.LOGIN_FAILED, {"reason": "no_credential"})

        if not password_hashing.verify_password(credential.password_hash, presented.password):
            credential.failed_login_attempts += 1
            credential.last_failed_at = now
            locked = credential.failed_login_attempts >= _settings.auth_lockout_failures
            if locked:
                credential.locked_until = now + dt.timedelta(minutes=_settings.auth_lockout_minutes)
                auth_events.record(
                    db,
                    AuthEventKind.LOCKED_OUT,
                    user_id=user.id,
                    dealership_id=user.tenant_id,
                    ip_address=presented.ip_address,
                    details={"until": credential.locked_until.isoformat()},
                )
            raise refuse(AuthEventKind.LOGIN_FAILED, {"reason": "password"})

        if user.status in _INACTIVE or not access_window_open(user):
            raise refuse(AuthEventKind.LOGIN_FAILED, {"reason": "inactive"})

        if password_hashing.needs_rehash(credential.password_hash):
            credential.password_hash = password_hashing.hash_password(presented.password)
            credential.updated_by = user.id
            auth_events.record(
                db,
                AuthEventKind.CREDENTIAL_REHASHED,
                user_id=user.id,
                dealership_id=user.tenant_id,
                ip_address=presented.ip_address,
            )
        # Not cleared here: the counter is cleared by a completed login
        # (`login_completed`), so wrong MFA answers accumulate on the
        # account like wrong passwords do.
        db.flush()
        return user


local_password_provider = LocalPasswordProvider()


def provider_for(db: Session, dealership_id: uuid.UUID) -> IdentityProvider:
    """FR-AU-02/03: the provider is the dealership's, never the user's. Only
    `local` exists in v1, so the lookup is the seam, not a choice yet."""

    read_auth_config(db, dealership_id)
    return local_password_provider


# --- dealership auth config -----------------------------------------------------------------


def read_auth_config(db: Session, dealership_id: uuid.UUID) -> DealershipAuthConfig:
    """The config as it stands, without writing: a Betrieb that never set
    one reads as the defaults (local, enforced, version 1) — the row is
    created by the first PUT (`auth_config_for`)."""

    row = db.scalar(select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == dealership_id))
    if row is not None:
        return row
    return DealershipAuthConfig(dealership_id=dealership_id, auth_method=AuthMethod.LOCAL, mfa_enforced=True, version=1)


def auth_config_for(db: Session, dealership_id: uuid.UUID) -> DealershipAuthConfig:
    """The row to WRITE, locked for the rest of the transaction: created with
    the defaults (local sign-in, MFA enforced, FR-AU-11) by the first PUT.
    Every read path uses `read_auth_config`, which never inserts.

    KAN-393: two first PUTs at once both found no row; the second insert hit
    the unique constraint at an unguarded flush, a 500. The insert now runs
    in a savepoint: the loser waits for the winner's commit, its insert
    fails, and it reads and locks the winner's row — whose version its
    If-Match is then checked against (409 when the winner changed it). The
    lock also keeps two PUTs on an existing row from both passing the
    version check."""

    stmt = (
        select(DealershipAuthConfig)
        .where(DealershipAuthConfig.dealership_id == dealership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = db.scalar(stmt)
    if row is not None:
        return row
    db.flush()  # so the except below can only see this row's INSERT
    try:
        with db.begin_nested():
            db.add(DealershipAuthConfig(dealership_id=dealership_id))
    except IntegrityError:
        pass  # a concurrent first PUT inserted it; its committed row is read below
    row = db.scalar(stmt)
    assert row is not None, "DealershipAuthConfig row missing after its first INSERT or the concurrent winner's"
    return row


# --- passwords ---------------------------------------------------------------------------------


def _dealership_names(db: Session, user: User) -> tuple[str, ...]:
    dealership = db.get(Dealership, user.tenant_id)
    if dealership is None:
        return ()
    return tuple(name for name in (dealership.legal_name, dealership.trade_name) if name)


def set_password(
    db: Session, *, user: User, password: str, actor_id: uuid.UUID, ip_address: str, event: AuthEventKind
) -> Credential:
    """The one way a password is written (FR-AU-10): by its owner, through
    an invitation or a reset. Policy-checked (FR-AU-04…06), the last five
    hashes refused, lockout cleared, the breach-check outcome recorded."""

    verdict = validate_new_password(
        password,
        dealership_names=_dealership_names(db, user),
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
    )
    credential = _credential_of(db, user.id)
    previous = [credential.password_hash, *credential.password_history] if credential is not None else []
    for old_hash in previous[:PASSWORD_HISTORY_DEPTH]:
        if password_hashing.verify_password(old_hash, password):
            raise BadRequestError("This password was used recently — choose a new one.", details={"rule": "reused"})
    new_hash = password_hashing.hash_password(password)
    now = utcnow()
    if credential is None:
        credential = Credential(user_id=user.id, password_hash=new_hash, created_by=actor_id, updated_by=actor_id)
        db.add(credential)
    else:
        credential.password_history = previous[: PASSWORD_HISTORY_DEPTH - 1]
        credential.password_hash = new_hash
        credential.updated_by = actor_id
    credential.password_set_at = now
    credential.breach_check_pending = verdict.breach_check_pending
    credential.failed_login_attempts = 0
    credential.last_failed_at = None
    credential.locked_until = None
    db.flush()
    auth_events.record(
        db, event, user_id=user.id, dealership_id=user.tenant_id, actor_id=actor_id, ip_address=ip_address
    )
    if verdict.breach_check_pending:
        auth_events.record(
            db,
            AuthEventKind.BREACH_CHECK_UNAVAILABLE,
            user_id=user.id,
            dealership_id=user.tenant_id,
            ip_address=ip_address,
        )
    return credential


def login_status(db: Session, user: User) -> dict:
    """What a manager may see (FR-AU-10): whether a password exists and
    whether the account is locked — never the password or its hash."""

    credential = _credential_of(db, user.id)
    now = utcnow()
    locked = credential is not None and credential.locked_until is not None and credential.locked_until > now
    return {
        "has_password": credential is not None,
        "locked_until": credential.locked_until if locked and credential is not None else None,
        "failed_login_attempts": credential.failed_login_attempts if credential is not None else 0,
    }


# --- invitation and reset ----------------------------------------------------------------------


def _language_of(db: Session, user: User) -> str | None:
    preference = preference_service.get_preference(db, user_id=user.id, scope="ui")
    if preference is None:
        return None
    language = (preference.payload or {}).get("uiLanguage")
    return language if isinstance(language, str) else None


def _deliver(
    db: Session, mailer: PlatformMailer, *, user: User, mail: Mail, actor_id: uuid.UUID | None, ip_address: str
) -> None:
    try:
        mailer.send(mail)
    except MailDeliveryError as exc:
        # FR-AU-15: not fire-and-forget. The event is what platform staff see;
        # the ERROR line is what the alarm fires on.
        logger.error("platform_mail_delivery_failed", extra={"user_id": str(user.id), "reason": str(exc)})
        auth_events.record(
            db,
            AuthEventKind.MAIL_DELIVERY_FAILED,
            user_id=user.id,
            dealership_id=user.tenant_id,
            actor_id=actor_id,
            ip_address=ip_address,
            details={"subject": mail.subject},
        )


def issue_invitation(
    db: Session, mailer: PlatformMailer, *, user: User, actor_id: uuid.UUID | None, ip_address: str
) -> None:
    """FR-AU-13: a single-use 14-day token, mailed by the platform sender.
    The raw token lives in the mail only."""

    raw = auth_tokens.issue(db, user_id=user.id, kind=AuthTokenKind.INVITATION, actor_id=actor_id)
    user.invited_at = utcnow()  # KAN-200: when the (latest) invitation went out
    dealership = db.get(Dealership, user.tenant_id)
    link = f"{_settings.app_base_url}/invitations/accept?token={raw}"
    text = mail_texts.invitation(
        language=_language_of(db, user),
        first_name=user.first_name,
        dealership=dealership.legal_name if dealership else "Nexotec",
        link=link,
    )
    auth_events.record(
        db,
        AuthEventKind.INVITATION_ISSUED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
    )
    _deliver(
        db,
        mailer,
        user=user,
        mail=Mail(to=user.email, subject=text.subject, text=text.body),
        actor_id=actor_id,
        ip_address=ip_address,
    )


@dataclasses.dataclass(frozen=True)
class PendingMail:
    """A mail the route hands to a background task after the response has
    gone out — the reset request's timing must not say whether a mail is
    being sent (FR-AU-16)."""

    mail: Mail
    user_id: uuid.UUID
    dealership_id: uuid.UUID


def request_password_reset(db: Session, *, email: str, ip_address: str) -> PendingMail | None:
    """FR-AU-14/16: the same outcome whether or not the address exists — the
    caller answers 202 either way, the work done in the request is the same
    shape (a token hash and a mail text are built in both cases), and the
    mail itself leaves after the response through `send_pending_mail`."""

    user = find_user_by_email(db, email)
    if user is None or user.status in _INACTIVE:
        # Same work, nowhere to send: the hash and the text a real request
        # builds, and the same single event.
        auth_tokens.token_hash_of(auth_tokens.new_raw_token())
        mail_texts.password_reset(language=None, first_name="", link="")
        auth_events.record(
            db, AuthEventKind.PASSWORD_RESET_REQUESTED, ip_address=ip_address, details={"matched": False}
        )
        return None
    raw = auth_tokens.issue(db, user_id=user.id, kind=AuthTokenKind.PASSWORD_RESET, actor_id=None)
    link = f"{_settings.app_base_url}/password-reset/confirm?token={raw}"
    text = mail_texts.password_reset(language=_language_of(db, user), first_name=user.first_name, link=link)
    auth_events.record(
        db,
        AuthEventKind.PASSWORD_RESET_REQUESTED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        ip_address=ip_address,
        details={"matched": True},
    )
    return PendingMail(
        mail=Mail(to=user.email, subject=text.subject, text=text.body),
        user_id=user.id,
        dealership_id=user.tenant_id,
    )


def send_pending_mail(mailer: PlatformMailer, pending: PendingMail | None, *, bind, ip_address: str) -> None:
    """The background half of `request_password_reset`: sends, and on a
    delivery failure records the `mail_delivery_failed` event in a session
    of its own (the request's is closed by now)."""

    if pending is None:
        return
    try:
        mailer.send(pending.mail)
    except MailDeliveryError as exc:
        logger.error("platform_mail_delivery_failed", extra={"user_id": str(pending.user_id), "reason": str(exc)})
        db = sessionmaker(bind=bind)()
        try:
            auth_events.record(
                db,
                AuthEventKind.MAIL_DELIVERY_FAILED,
                user_id=pending.user_id,
                dealership_id=pending.dealership_id,
                ip_address=ip_address,
                details={"subject": pending.mail.subject},
            )
            db.commit()
        finally:
            db.close()


def confirm_password_reset(db: Session, *, raw_token: str, password: str, ip_address: str) -> User:
    """FR-AU-14: consumes the token atomically, sets the password, clears the
    lockout and ends every session of the user."""

    user = _token_user_locked(db, raw_token=raw_token, kind=AuthTokenKind.PASSWORD_RESET)
    token = auth_tokens.consume(db, raw=raw_token, kind=AuthTokenKind.PASSWORD_RESET) if user is not None else None
    if token is None or user is None or user.status in _INACTIVE:
        raise BadRequestError("This link is no longer valid — request a new one.", details={"rule": "link"})
    set_password(
        db, user=user, password=password, actor_id=user.id, ip_address=ip_address, event=AuthEventKind.PASSWORD_RESET
    )
    # A reset reaches the mailbox the invitation did: an invited user who
    # sets a password this way is active from here on — and an invitation
    # still open must not stay a second way in.
    _activate(user)
    auth_tokens.consume_all_for_user(db, user_id=user.id, kinds=(AuthTokenKind.INVITATION,))
    _revoke_all(db, user=user, reason="password_reset", ip_address=ip_address)
    return user


def accept_invitation(db: Session, *, raw_token: str, password: str, ip_address: str) -> User:
    """FR-AU-13: consumes the token atomically, sets the first password and
    activates the invited user. The user row is locked before the token is
    consumed (`_token_user_locked`)."""

    user = _token_user_locked(db, raw_token=raw_token, kind=AuthTokenKind.INVITATION)
    token = auth_tokens.consume(db, raw=raw_token, kind=AuthTokenKind.INVITATION) if user is not None else None
    if token is None or user is None or user.status in _INACTIVE:
        raise BadRequestError("This invitation is no longer valid — ask your business for a new one.", details={"rule": "link"})
    set_password(
        db, user=user, password=password, actor_id=user.id, ip_address=ip_address, event=AuthEventKind.PASSWORD_SET
    )
    _activate(user)
    # A re-issued invitation is a reset by another name: every session the
    # user still holds ends with it (FR-AU-14), and an open reset link too.
    _revoke_all(db, user=user, reason="invitation", ip_address=ip_address)
    auth_tokens.consume_all_for_user(db, user_id=user.id, kinds=(AuthTokenKind.PASSWORD_RESET,))
    auth_events.record(
        db, AuthEventKind.INVITATION_ACCEPTED, user_id=user.id, dealership_id=user.tenant_id, ip_address=ip_address
    )
    return user


def login_completed(db: Session, *, user: User) -> None:
    """A session was minted: the account's failure counter and lock clear,
    and the user's last login is stamped (KAN-200, Users → Lifecycle)."""

    user.last_login_at = utcnow()
    credential = _credential_of(db, user.id)
    if credential is None:
        return
    credential.failed_login_attempts = 0
    credential.last_failed_at = None
    credential.locked_until = None


def access_window_open(user: User, *, today: dt.date | None = None) -> bool:
    """KAN-200 (FR-A-12): a user whose `valid_from` is still to come, or
    whose `valid_to` has passed (the nightly job deactivates them; until it
    runs, the window itself refuses), cannot sign in. Calendar days in the
    platform zone, like the session's absolute end."""

    day = today if today is not None else dt.datetime.now(ZoneInfo(_settings.session_timezone)).date()
    if user.valid_from is not None and day < user.valid_from:
        return False
    return not (user.valid_to is not None and day > user.valid_to)


def second_step_failed(db: Session, *, user: User, ip_address: str = "") -> None:
    """EVERY wrong answer at the MFA step is a failure on the account axis,
    like a wrong password — and the password step does not clear that axis,
    only a completed login does. So a holder of the password gets the same
    backoff (from the third failure) and lockout (at the fifth) as a
    password guesser, however many challenges they start (the fourth review
    showed seventy-five guesses with per-challenge counting alone)."""

    credential = _credential_of(db, user.id)
    if credential is None:
        return
    credential.failed_login_attempts += 1
    credential.last_failed_at = utcnow()
    if credential.failed_login_attempts >= _settings.auth_lockout_failures:
        credential.locked_until = utcnow() + dt.timedelta(minutes=_settings.auth_lockout_minutes)
        auth_events.record(
            db,
            AuthEventKind.LOCKED_OUT,
            user_id=user.id,
            dealership_id=user.tenant_id,
            ip_address=ip_address,
            details={"until": credential.locked_until.isoformat(), "step": "mfa"},
        )


def account_throttled(db: Session, *, user: User) -> bool:
    """The account axis as the MFA step sees it: locked, or inside the
    backoff after recent failures — a challenge opened before the lock must
    not complete through it."""

    return _account_throttled(_credential_of(db, user.id), utcnow())


def email_changed(
    db: Session, mailer: PlatformMailer, *, user: User, old_email: str, actor_id: uuid.UUID, ip_address: str
) -> None:
    """The address is the sign-in and the recovery channel, so changing it is
    a credential change: every open invitation and reset token ends (a link
    mailed to the old address must not activate the account), every
    session ends, the event names the actor, and the OLD address is told
    (FR-AU-26's spirit: the person whose account changed hears of it)."""

    auth_tokens.consume_all_for_user(
        db, user_id=user.id, kinds=(AuthTokenKind.INVITATION, AuthTokenKind.PASSWORD_RESET)
    )
    _revoke_all(db, user=user, reason="email_changed", ip_address=ip_address)
    auth_events.record(
        db,
        AuthEventKind.EMAIL_CHANGED,
        user_id=user.id,
        dealership_id=user.tenant_id,
        actor_id=actor_id,
        ip_address=ip_address,
        details={"from": old_email, "to": user.email},
    )
    text = mail_texts.email_changed(language=_language_of(db, user), first_name=user.first_name, new_email=user.email)
    _deliver(
        db,
        mailer,
        user=user,
        mail=Mail(to=old_email, subject=text.subject, text=text.body),
        actor_id=actor_id,
        ip_address=ip_address,
    )


def _token_user_locked(db: Session, *, raw_token: str, kind: AuthTokenKind) -> User | None:
    """The user a live token names, its row locked FOR NO KEY UPDATE (and
    re-read) before the front door consumes the token — None when the token
    is not live or names no user.

    KAN-400 / KAN-393: the front door used to consume the token (locking its
    row) and then write the user row unlocked, while a manager's e-mail
    change holds the user row (KAN-380) and then ends the user's tokens:
    opposite orders, a deadlock and a 500. Unlocked, the acceptance also
    wrote `version = <read> + 1` over a PATCH that committed meanwhile, and
    two front-door writes of one user (an invitation and a reset at once)
    both inserted the first `credential`. One order now — user row, then
    token, then credential — like every user-administration route."""

    token = auth_tokens.peek(db, raw=raw_token, kind=kind)
    if token is None:
        return None
    return db.scalar(
        select(User)
        .where(User.id == token.user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )


def _activate(user: User) -> None:
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
        user.updated_by = user.id
        user.version += 1


def _revoke_all(db: Session, *, user: User, reason: str, ip_address: str) -> None:
    ended = auth_sessions.revoke_all_for_user(db, user_id=user.id, reason=reason)
    if ended:
        auth_events.record(
            db,
            AuthEventKind.SESSION_REVOKED,
            user_id=user.id,
            dealership_id=user.tenant_id,
            ip_address=ip_address,
            details={"count": ended, "reason": reason},
        )
