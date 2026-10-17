"""Sessions (FR-AU-17…19, KAN-196): one `session` row per login, the local
revocation check that replaced Zitadel's live userinfo call. Idle timeout and
absolute end from settings (Anto, 2026-10-17: two hours idle; absolute at the
next midnight Europe/Zurich, so nobody is cut off mid-configuration by a
fixed 24 h). `touch` extends the idle end on activity, at most once a minute
so a busy screen does not write a row per request."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow  # re-exported for the API's cookie max-age
from app.core.config import get_settings
from app.platform.models.auth_session import AuthSession

_settings = get_settings()
_TOUCH_GRANULARITY = dt.timedelta(minutes=1)


def idle_timeout() -> dt.timedelta:
    return dt.timedelta(minutes=_settings.session_idle_minutes)


def absolute_expiry(now: dt.datetime | None = None) -> dt.datetime:
    """The next midnight in the platform zone, as an aware UTC datetime."""

    zone = ZoneInfo(_settings.session_timezone)
    local_now = (now or utcnow()).astimezone(zone)
    next_midnight = (local_now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.astimezone(dt.UTC)


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    dealership_id: uuid.UUID,
    device_summary: str,
    ip_address: str,
    amr: tuple[str, ...] | list[str],
) -> AuthSession:
    now = utcnow()
    row = AuthSession(
        user_id=user_id,
        dealership_id=dealership_id,
        device_summary=device_summary[:255],
        ip_address=ip_address[:45],
        amr=list(amr),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + idle_timeout(),
        absolute_expires_at=absolute_expiry(now),
    )
    db.add(row)
    db.flush()
    return row


def get(db: Session, session_id: uuid.UUID) -> AuthSession | None:
    return db.get(AuthSession, session_id)


def touch(db: Session, row: AuthSession) -> bool:
    """Extends the idle end. Returns whether a write happened."""

    now = utcnow()
    if now - row.last_seen_at < _TOUCH_GRANULARITY:
        return False
    row.last_seen_at = now
    row.idle_expires_at = min(now + idle_timeout(), row.absolute_expires_at)
    return True


def revoke(db: Session, row: AuthSession, *, reason: str) -> None:
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_reason = reason[:32]


def revoke_all_for_user(
    db: Session, *, user_id: uuid.UUID, reason: str, except_session_id: uuid.UUID | None = None
) -> int:
    """Ends every live session of the user — a reset (FR-AU-14), a
    deactivation (FR-AU-17), a manager's revoke. Returns how many ended."""

    now = utcnow()
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:32])
    )
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    result = db.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def list_for_user(db: Session, *, user_id: uuid.UUID) -> list[AuthSession]:
    return list(
        db.scalars(
            select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at.desc())
        ).all()
    )
