"""Outbox writer + operator queries (PR-4, ADR-006). See
app.core.outbox_worker for the poller and app.core.consumer for the
consumer harness — this module only writes new messages and answers
questions about existing ones.

Session contract for publish(): it takes the CALLER's own session and only
adds + flushes — it never commits. The business write and the outbox row
must land in the caller's own transaction, so whether they land together
is entirely up to the caller doing its usual db.commit(). This is Decision
2: explicit `outbox.publish(db, event)` in the same session as the
business write, never an implicit ORM/session hook.
"""

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.uuid7 import uuid7


@dataclasses.dataclass(frozen=True)
class OutboxEvent:
    """The envelope, minus what the outbox itself assigns (id, status,
    attempts, timestamps). `event_type` should read `context.entity.
    past_tense_verb` (e.g. `customer.merged`) once real events land in
    PR-5 — this PR ships no real event types, only a test-only probe.
    """

    event_type: str
    tenant_id: uuid.UUID | None
    producer: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    event_version: int = 1
    correlation_id: uuid.UUID | None = None
    causation_id: uuid.UUID | None = None
    occurred_at: dt.datetime | None = None


def publish(db: Session, event: OutboxEvent) -> OutboxMessage:
    """Adds and flushes one OutboxMessage into the caller's own session —
    does not commit. The caller's own db.commit() is what makes the
    business write and this row atomic; that's the entire point of the
    outbox, so there is deliberately no commit call in this function.
    """

    message = OutboxMessage(
        id=uuid7(),
        event_type=event.event_type,
        event_version=event.event_version,
        occurred_at=event.occurred_at or utcnow(),
        tenant_id=event.tenant_id,
        producer=event.producer,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        correlation_id=event.correlation_id or uuid7(),
        causation_id=event.causation_id,
        payload=event.payload,
        status=OutboxStatus.PENDING,
        attempts=0,
        next_attempt_at=utcnow(),
    )
    db.add(message)
    db.flush()
    return message


def replay(db: Session, message_id: uuid.UUID) -> OutboxMessage:
    """Resets a dead message to pending with a fresh attempt budget.
    Explicit call only — never an automatic sweep (Decision 5). Attempts
    reset to 0 rather than carried forward: a human presumably fixed
    whatever was wrong, so the message gets a full five attempts again,
    not whatever was left when it dead-lettered.
    """

    message = db.get(OutboxMessage, message_id)
    if message is None:
        raise ValueError(f"No outbox message with id {message_id}.")
    if message.status != OutboxStatus.DEAD:
        raise ValueError(f"Message {message_id} is {message.status.value}, not dead — nothing to replay.")
    message.status = OutboxStatus.PENDING
    message.attempts = 0
    message.next_attempt_at = utcnow()
    message.last_error = None
    db.flush()
    return message


def oldest_pending_age_seconds(db: Session) -> float | None:
    """Outbox lag (Decision 8) — how long the oldest still-pending message
    has been waiting. None means the outbox is fully drained. Queryable
    now; WP-2 wires this to a real alarm later.
    """

    oldest_created_at = db.scalar(
        select(func.min(OutboxMessage.created_at)).where(OutboxMessage.status == OutboxStatus.PENDING)
    )
    if oldest_created_at is None:
        return None
    return (utcnow() - oldest_created_at).total_seconds()


def dead_letter_count(db: Session) -> int:
    """Dead-letter depth (Decision 8) — queryable now; WP-2 wires this to
    a real alarm later.
    """

    # COUNT(*) never returns SQL NULL, but db.scalar()'s return type is
    # `int | None` regardless of what the query is — the `or 0` is for
    # mypy, not for a case that happens at runtime.
    count = db.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.status == OutboxStatus.DEAD))
    return count or 0
