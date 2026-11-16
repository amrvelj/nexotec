"""Support sessions (KAN-221 PR-2; D-A-05, D-A-24, FR-A-15, FR-A-17, FR-A-18).

- **Start** — platform staff only, from a session signed in with a second
  factor (D-A-36) and never under an impersonation (the route's gate). The
  target is an active user of a Betrieb who does not hold `platform_admin`
  (FR-A-17: `platform_admin` cannot be impersonated) and is not the staff
  member. A stated reason is mandatory. 60 minutes, then it is over.
- **One at a time:** a staff member with a session still running is refused
  a second (409 `support_session_already_open`).
- **Expiry:** a session nobody ended is over at `expires_at`. Reads derive
  it at once (`state_of`); the nightly `platform.support_sessions.close_expired`
  and the staff member's next start write it into the row — `ended_by
  timeout`, audited as a system write with reason `support_session_timed_out`.
- **End** — by the staff member or by the user (FR-A-18: the user ends it
  from the banner). Anyone else is told it does not exist (404, rule 7). A
  session already over is 409 `support_session_ended`.
- Every start and end is an audit row on the Betrieb (`entity_type
  support_session`), naming the staff member as the actor — never the
  dealer.

Not yet: the `acting_for` token (PR 3), the FR-A-17 exclusions and the
security notices (PR 4), the logs (PR 5), the banner and screens (PR 6).
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import AccessRole, Principal
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.platform.identity.models.support_session import (
    SUPPORT_SESSION_MINUTES,
    SupportSession,
    SupportSessionEndedBy,
)
from app.platform.identity.models.user import User, UserStatus
from app.platform.identity.schemas.support_session import SupportSessionRead

ENTITY_TYPE = "support_session"
# The audit reason of an ending nobody wrote: the 60 minutes ran out.
TIMEOUT_REASON = "support_session_timed_out"
OPEN_CONSTRAINT = "uq_support_session_staff_open"


def _violated(exc: IntegrityError) -> str | None:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return str(name)
    return OPEN_CONSTRAINT if OPEN_CONSTRAINT in str(exc.orig) or "support_session.staff_user_id" in str(exc.orig) else None


def state_of(row: SupportSession, now: dt.datetime) -> tuple[dt.datetime | None, SupportSessionEndedBy | None, bool]:
    """(ended at, ended by, still running) — a session nobody ended is over
    by `timeout` from `expires_at` on (the model's docstring)."""

    if row.ended_at is not None:
        return row.ended_at, row.ended_by, False
    if now >= row.expires_at:
        return row.expires_at, SupportSessionEndedBy.TIMEOUT, False
    return None, None, True


def to_read(row: SupportSession, now: dt.datetime | None = None) -> SupportSessionRead:
    ended_at, ended_by, active = state_of(row, now or utcnow())
    return SupportSessionRead(
        id=row.id,
        staff_user_id=row.staff_user_id,
        target_user_id=row.target_user_id,
        dealership_id=row.dealership_id,
        reason=row.reason,
        started_at=row.started_at,
        expires_at=row.expires_at,
        ended_at=ended_at,
        ended_by=ended_by,
        active=active,
    )


def _audit_end(db: Session, row: SupportSession, *, actor_id: uuid.UUID | None, reason: str | None = None) -> None:
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        tenant_id=row.dealership_id,
        action="end",
        actor_id=actor_id,
        reason=reason,
        before={"endedAt": None},
        after={
            "endedAt": row.ended_at.isoformat() if row.ended_at else None,
            "endedBy": row.ended_by.value if row.ended_by else None,
        },
    )


def _close_timed_out(db: Session, row: SupportSession) -> None:
    """Write a run-out session's ending into its row: at `expires_at`, by
    `timeout`, audited as a system write (no actor — nobody ended it)."""

    row.ended_at = row.expires_at
    row.ended_by = SupportSessionEndedBy.TIMEOUT
    row.updated_by = None
    _audit_end(db, row, actor_id=None, reason=TIMEOUT_REASON)


def close_timed_out_sessions(db: Session, now: dt.datetime | None = None) -> int:
    """The expiry job's step (`platform.support_sessions.close_expired`):
    every session past its `expires_at` that nobody ended gets its ending
    written, so the row — and the Betrieb's export of it — says so. Reads
    derive the same ending before the job has run (`state_of`). The caller
    commits. Returns how many were closed."""

    rows = db.scalars(
        select(SupportSession)
        .where(SupportSession.ended_at.is_(None), SupportSession.expires_at <= (now or utcnow()))
        .order_by(SupportSession.id)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        _close_timed_out(db, row)
    return len(rows)


def start_support_session(
    db: Session, *, staff: Principal, target_user_id: uuid.UUID, reason: str
) -> SupportSession:
    target = db.get(User, target_user_id)
    if target is None:
        raise NotFoundError("User not found.")
    if target.id == staff.user_id:
        raise UnprocessableEntityError(
            "A staff member cannot start a support session for themselves.",
            details={"reason": "support_target_self"},
        )
    if AccessRole.PLATFORM_ADMIN.value in (target.access_roles or []):
        raise UnprocessableEntityError(
            "A platform_admin cannot be impersonated (FR-A-17).",
            details={"reason": "support_target_platform_admin"},
        )
    if target.status != UserStatus.ACTIVE:
        raise UnprocessableEntityError(
            "Only an active user can be supported in a session.",
            details={"reason": "support_target_not_active"},
        )

    now = utcnow()
    still_open = db.scalars(
        select(SupportSession)
        .where(SupportSession.staff_user_id == staff.user_id, SupportSession.ended_at.is_(None))
        .with_for_update()
    ).first()
    if still_open is not None:
        if now < still_open.expires_at:
            raise ConflictError(
                "End the running support session before starting another.",
                details={"reason": "support_session_already_open", "supportSessionId": str(still_open.id)},
            )
        _close_timed_out(db, still_open)

    row = SupportSession(
        staff_user_id=staff.user_id,
        target_user_id=target.id,
        dealership_id=target.tenant_id,
        reason=reason,
        started_at=now,
        expires_at=now + dt.timedelta(minutes=SUPPORT_SESSION_MINUTES),
        created_by=staff.user_id,
        updated_by=staff.user_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _violated(exc) != OPEN_CONSTRAINT:
            raise
        # Two starts of one staff member at once: the second is the first's twin.
        raise ConflictError(
            "End the running support session before starting another.",
            details={"reason": "support_session_already_open"},
        ) from exc
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        tenant_id=row.dealership_id,
        action="start",
        actor_id=staff.user_id,
        after={
            "staffUserId": str(staff.user_id),
            "targetUserId": str(target.id),
            "reason": reason,
            "expiresAt": row.expires_at.isoformat(),
        },
    )
    db.commit()
    db.refresh(row)
    return row


def end_support_session(db: Session, *, session_id: uuid.UUID, principal: Principal) -> SupportSession:
    row = db.get(SupportSession, session_id, with_for_update=True)
    # Only the two people in it know it exists (rule 7: 404, never 403).
    if row is None or principal.user_id not in (row.staff_user_id, row.target_user_id):
        raise NotFoundError("Support session not found.")
    now = utcnow()
    _ended_at, ended_by, active = state_of(row, now)
    if not active:
        raise ConflictError(
            "This support session is already over.",
            details={"reason": "support_session_ended", "endedBy": ended_by.value if ended_by else None},
        )
    row.ended_at = now
    row.ended_by = SupportSessionEndedBy.STAFF if principal.user_id == row.staff_user_id else SupportSessionEndedBy.USER
    row.updated_by = principal.user_id
    _audit_end(db, row, actor_id=principal.user_id)
    db.commit()
    db.refresh(row)
    return row
