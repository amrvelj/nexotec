"""Sessions (FR-AU-17…19, KAN-196): one `session` row per login, the local
revocation check (no live call to an identity provider). Idle timeout and
absolute end from settings (Anto, 2026-10-17: two hours idle; absolute at the
next midnight Europe/Zurich, so nobody is cut off mid-configuration by a
fixed 24 h). A Betrieb may set a stricter policy (KAN-229, D-A-37):
`session_policy` resolves it at login, and the row keeps its idle window
(`idle_minutes`) and absolute end. `touch` extends the idle end on activity, at most once a minute
so a busy screen does not write a row per request."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow  # re-exported for the API's cookie max-age
from app.core.config import get_settings
from app.platform.identity.models.auth_session import AuthSession
from app.platform.identity.services import push_subscriptions
from app.platform.identity.services.session_policy import SessionPolicy, absolute_end, platform_policy

_settings = get_settings()
_TOUCH_GRANULARITY = dt.timedelta(minutes=1)


def idle_timeout(row: AuthSession | None = None) -> dt.timedelta:
    """The row's own idle window (fixed from its Betrieb's policy), else the
    platform's."""

    minutes = row.idle_minutes if row is not None and row.idle_minutes is not None else None
    return dt.timedelta(minutes=minutes if minutes is not None else _settings.session_idle_minutes)


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
    policy: SessionPolicy | None = None,
) -> AuthSession:
    """`policy` is the Betrieb's effective session policy
    (`session_policy.effective_policy`); None = the platform's."""

    now = utcnow()
    policy = policy or platform_policy()
    row = AuthSession(
        user_id=user_id,
        dealership_id=dealership_id,
        device_summary=device_summary[:255],
        ip_address=ip_address[:45],
        amr=list(amr),
        created_at=now,
        last_seen_at=now,
        idle_minutes=policy.idle_minutes,
        idle_expires_at=now + dt.timedelta(minutes=policy.idle_minutes),
        absolute_expires_at=absolute_end(started_at=now, policy=policy, next_midnight=absolute_expiry(now)),
    )
    row.idle_expires_at = min(row.idle_expires_at, row.absolute_expires_at)
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
    row.idle_expires_at = min(now + idle_timeout(row), row.absolute_expires_at)
    return True


def revoke(db: Session, row: AuthSession, *, reason: str) -> None:
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_reason = reason[:32]
    # FR-A-49 (KAN-216): a device whose session is revoked loses its Web Push
    # subscription, in the revocation's own transaction.
    push_subscriptions.delete_for_session(db, row.id)


def revoke_all_for_user(
    db: Session, *, user_id: uuid.UUID, reason: str, except_session_id: uuid.UUID | None = None
) -> int:
    """Ends every live session of the user — a reset (FR-AU-14), a
    deactivation (FR-AU-17), platform staff's revoke (D-A-16). Returns how many ended."""

    now = utcnow()
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason[:32])
    )
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    result = db.execute(stmt)
    # FR-A-49 (KAN-216): every revoked session's Web Push subscription goes.
    push_subscriptions.delete_for_revoked_sessions(db, user_id=user_id)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def is_new_device(db: Session, row: AuthSession | None) -> bool:
    """Whether this session's device (its user agent, as `device_summary`
    holds it) matches none of the user's earlier sessions — and there was an
    earlier one: the first sign-in ever is no stranger's (KAN-216,
    `security.new_device_login`). A browser update changes the user agent and
    reads as a new device; there is no device cookie to tell otherwise."""

    if row is None:
        return False
    earlier = set(
        db.scalars(
            select(AuthSession.device_summary).where(AuthSession.user_id == row.user_id, AuthSession.id != row.id)
        ).all()
    )
    return bool(earlier) and row.device_summary not in earlier


def list_for_user(db: Session, *, user_id: uuid.UUID) -> list[AuthSession]:
    return list(
        db.scalars(
            select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at.desc())
        ).all()
    )
