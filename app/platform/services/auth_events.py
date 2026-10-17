"""The append-only auth event stream (FR-AU-20/21, KAN-196) and the per-IP
throttle that reads it (FR-AU-08). `record` only adds; nothing updates or
deletes a row. Never a secret in `details`."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.platform.models.auth_event import AuthEvent, AuthEventKind


def record(
    db: Session,
    kind: AuthEventKind,
    *,
    user_id: uuid.UUID | None = None,
    dealership_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    ip_address: str = "",
    details: dict[str, Any] | None = None,
) -> AuthEvent:
    event = AuthEvent(
        kind=kind,
        user_id=user_id,
        dealership_id=dealership_id,
        actor_id=actor_id,
        ip_address=ip_address[:45],
        occurred_at=utcnow(),
        details=details,
    )
    db.add(event)
    db.flush()
    return event


def recent_failures_from_ip(db: Session, *, ip_address: str, window: dt.timedelta) -> tuple[int, dt.datetime | None]:
    """How many login failures this IP produced inside the window, and the
    latest one — the per-IP axis of the exponential backoff."""

    since = utcnow() - window
    row = db.execute(
        select(func.count(AuthEvent.id), func.max(AuthEvent.occurred_at)).where(
            AuthEvent.ip_address == ip_address,
            AuthEvent.kind == AuthEventKind.LOGIN_FAILED,
            AuthEvent.occurred_at >= since,
        )
    ).one()
    return int(row[0] or 0), row[1]


def list_for_dealership(db: Session, *, dealership_id: uuid.UUID, limit: int = 200) -> list[AuthEvent]:
    return list(
        db.scalars(
            select(AuthEvent)
            .where(AuthEvent.dealership_id == dealership_id)
            .order_by(AuthEvent.occurred_at.desc(), AuthEvent.id.desc())
            .limit(limit)
        ).all()
    )
