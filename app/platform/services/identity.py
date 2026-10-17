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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError, UnauthorizedError
from app.platform.models.auth_event import AuthEventKind
from app.platform.models.auth_token import AuthTokenKind
from app.platform.models.credential import Credential
from app.platform.models.dealership import Dealership
from app.platform.models.dealership_auth_config import DealershipAuthConfig
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


class LocalPasswordProvider:
    """FR-AU-01's live implementation: e-mail + argon2id password."""

    def resolve(self, db: Session, presented: PresentedCredentials) -> User:
        now = utcnow()
        email = presented.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
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

        if user.status in _INACTIVE:
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
        credential.failed_login_attempts = 0
        credential.last_failed_at = None
        credential.locked_until = None
        db.flush()
        return user


local_password_provider = LocalPasswordProvider()


def provider_for(db: Session, dealership_id: uuid.UUID) -> IdentityProvider:
    """FR-AU-02/03: the provider is the dealership's, never the user's. Only
    `local` exists in v1, so the lookup is the seam, not a choice yet."""

    auth_config_for(db, dealership_id)
    return local_password_provider


# --- dealership auth config -----------------------------------------------------------------


def auth_config_for(db: Session, dealership_id: uuid.UUID) -> DealershipAuthConfig:
    """Created on first read with the defaults: local sign-in, MFA enforced
    (FR-AU-11)."""

    row = db.scalar(select(DealershipAuthConfig).where(DealershipAuthConfig.dealership_id == dealership_id))
    if row is None:
        row = DealershipAuthConfig(dealership_id=dealership_id)
        db.add(row)
        db.flush()
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
            raise BadRequestError("This password was used recently — choose a new one.")
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


def request_password_reset(db: Session, mailer: PlatformMailer, *, email: str, ip_address: str) -> None:
    """FR-AU-14/16: the same outcome whether or not the address exists — the
    caller answers 202 either way, and the work done is the same shape (a
    token hash and a mail text are built in both cases)."""

    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or user.status in _INACTIVE:
        # Same work, nowhere to send: the hash and the text a real request
        # builds, and the same single event.
        auth_tokens.token_hash_of(auth_tokens.new_raw_token())
        mail_texts.password_reset(language=None, first_name="", link="")
        auth_events.record(
            db, AuthEventKind.PASSWORD_RESET_REQUESTED, ip_address=ip_address, details={"matched": False}
        )
        return
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
    _deliver(
        db,
        mailer,
        user=user,
        mail=Mail(to=user.email, subject=text.subject, text=text.body),
        actor_id=None,
        ip_address=ip_address,
    )


def confirm_password_reset(db: Session, *, raw_token: str, password: str, ip_address: str) -> User:
    """FR-AU-14: consumes the token atomically, sets the password, clears the
    lockout and ends every session of the user."""

    token = auth_tokens.consume(db, raw=raw_token, kind=AuthTokenKind.PASSWORD_RESET)
    if token is None:
        raise BadRequestError("This link is no longer valid — request a new one.")
    user = db.get(User, token.user_id)
    if user is None or user.status in _INACTIVE:
        raise BadRequestError("This link is no longer valid — request a new one.")
    set_password(
        db, user=user, password=password, actor_id=user.id, ip_address=ip_address, event=AuthEventKind.PASSWORD_RESET
    )
    ended = auth_sessions.revoke_all_for_user(db, user_id=user.id, reason="password_reset")
    if ended:
        auth_events.record(
            db,
            AuthEventKind.SESSION_REVOKED,
            user_id=user.id,
            dealership_id=user.tenant_id,
            ip_address=ip_address,
            details={"count": ended, "reason": "password_reset"},
        )
    return user


def accept_invitation(db: Session, *, raw_token: str, password: str, ip_address: str) -> User:
    """FR-AU-13: consumes the token atomically, sets the first password and
    activates the invited user."""

    token = auth_tokens.consume(db, raw=raw_token, kind=AuthTokenKind.INVITATION)
    if token is None:
        raise BadRequestError("This invitation is no longer valid — ask your Betrieb for a new one.")
    user = db.get(User, token.user_id)
    if user is None or user.status in _INACTIVE:
        raise BadRequestError("This invitation is no longer valid — ask your Betrieb for a new one.")
    set_password(
        db, user=user, password=password, actor_id=user.id, ip_address=ip_address, event=AuthEventKind.PASSWORD_SET
    )
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
        user.updated_by = user.id
        user.version += 1
    auth_events.record(
        db, AuthEventKind.INVITATION_ACCEPTED, user_id=user.id, dealership_id=user.tenant_id, ip_address=ip_address
    )
    return user
