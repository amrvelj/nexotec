"""Single-use tokens (FR-AU-13/14, KAN-196): issued as 32 random bytes the
caller sends by mail or returns once, stored as their SHA-256. One live token
per (user, kind) — issuing consumes the previous. `consume` is one UPDATE
guarded by `consumed_at IS NULL AND expires_at > now`, so two concurrent
presentations cannot both win."""

import datetime as dt
import hashlib
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.platform.models.auth_token import AuthToken, AuthTokenKind

INVITATION_TTL = dt.timedelta(days=14)
PASSWORD_RESET_TTL = dt.timedelta(hours=1)
MFA_CHALLENGE_TTL = dt.timedelta(minutes=5)

_TTL = {
    AuthTokenKind.INVITATION: INVITATION_TTL,
    AuthTokenKind.PASSWORD_RESET: PASSWORD_RESET_TTL,
    AuthTokenKind.MFA_CHALLENGE: MFA_CHALLENGE_TTL,
}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash_of(raw: str) -> str:
    return _hash(raw)


def issue(db: Session, *, user_id: uuid.UUID, kind: AuthTokenKind, actor_id: uuid.UUID | None) -> str:
    """Returns the raw token — the one and only time it exists in clear."""

    now = utcnow()
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.kind == kind, AuthToken.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    raw = new_raw_token()
    db.add(
        AuthToken(
            user_id=user_id,
            kind=kind,
            token_hash=_hash(raw),
            created_at=now,
            expires_at=now + _TTL[kind],
            created_by=actor_id,
        )
    )
    db.flush()
    return raw


def consume(db: Session, *, raw: str, kind: AuthTokenKind) -> AuthToken | None:
    """Marks the token consumed and returns it, or None when it does not
    exist, is of another kind, has expired or was already consumed. Atomic:
    the UPDATE's WHERE is the check."""

    now = utcnow()
    token_id = db.scalar(
        update(AuthToken)
        .where(
            AuthToken.token_hash == _hash(raw),
            AuthToken.kind == kind,
            AuthToken.consumed_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(AuthToken.id)
    )
    if token_id is None:
        return None
    return db.get(AuthToken, token_id)


def peek(db: Session, *, raw: str, kind: AuthTokenKind) -> AuthToken | None:
    """The live token without consuming it — for the MFA step, which
    verifies a code against the challenge and consumes it only on success."""

    now = utcnow()
    token = db.scalar(select(AuthToken).where(AuthToken.token_hash == _hash(raw), AuthToken.kind == kind))
    if token is None or not token.is_live(now):
        return None
    return token
