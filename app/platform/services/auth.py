"""Interim internal login service (issue #8): Credential CRUD + password
authentication, account lockout, and the User-status gate on login. Kept
separate from app.core.auth (JWT/session-token shape) and app.core.security
(hashing primitives) — this module is the business logic that ties them to
User/Credential.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.platform.models.credential import Credential
from app.platform.models.user import User, UserStatus

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_DURATION_SECONDS = 15 * 60
_GENERIC_INVALID_CREDENTIALS = "Invalid email or password."
_LOCKED_MESSAGE = "Account is temporarily locked due to repeated failed login attempts. Try again later."
_INACTIVE_MESSAGE = "This account is suspended or deactivated. Contact your administrator."


def _as_aware_utc(value: dt.datetime) -> dt.datetime:
    """SQLite drops tzinfo on round-trip even for a DateTime(timezone=True)
    column (Postgres preserves it) — normalize so a freshly-computed
    timezone-aware `now` can always be compared against a value just read
    back from either backend.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def set_credential(db: Session, *, user: User, password: str, actor_id: uuid.UUID) -> Credential:
    existing = db.scalar(select(Credential).where(Credential.user_id == user.id))
    action = "credential_reset" if existing else "credential_set"

    if existing is not None:
        existing.password_hash = hash_password(password)
        existing.failed_login_attempts = 0
        existing.locked_until = None
        existing.updated_by = actor_id
        credential = existing
    else:
        credential = Credential(
            user_id=user.id,
            password_hash=hash_password(password),
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(credential)

    # Never logs the password or its hash — just the fact that a credential
    # was (re)set, for accountability on who can access this account.
    record_audit_event(
        db,
        entity_type="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        action=action,
        actor_id=actor_id,
    )
    db.commit()
    db.refresh(credential)
    return credential


def authenticate(db: Session, *, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    credential = db.scalar(select(Credential).where(Credential.user_id == user.id)) if user else None

    if user is None or credential is None:
        raise UnauthorizedError(_GENERIC_INVALID_CREDENTIALS)

    now = utcnow()
    if credential.locked_until is not None and _as_aware_utc(credential.locked_until) > now:
        raise UnauthorizedError(_LOCKED_MESSAGE)

    if not verify_password(password, credential.password_hash):
        credential.failed_login_attempts += 1
        if credential.failed_login_attempts >= _MAX_FAILED_ATTEMPTS:
            credential.locked_until = now + dt.timedelta(seconds=_LOCKOUT_DURATION_SECONDS)
        db.commit()
        raise UnauthorizedError(_GENERIC_INVALID_CREDENTIALS)

    if user.status in (UserStatus.SUSPENDED, UserStatus.DEACTIVATED):
        raise ForbiddenError(_INACTIVE_MESSAGE)

    credential.failed_login_attempts = 0
    credential.locked_until = None
    db.commit()
    return user
