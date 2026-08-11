"""Outbox poller/publisher (PR-4, Decision 4). One poll cycle claims a
batch, dispatches each claimed message, decides its next state, commits
once. See app.worker for the process that calls poll_once() in a loop.

SESSION BOUNDARIES — read this before touching this file. This is the one
place PR-4 can silently break at-least-once delivery.

- poll_once()'s own `db` argument is the ONLY session that ever claims
  rows: it runs SELECT ... FOR UPDATE SKIP LOCKED, and later, in that SAME
  transaction, updates every claimed row's status / attempts /
  next_attempt_at / last_error / published_at. That transaction commits
  exactly ONCE, at the end of poll_once(), after every claimed message has
  been dispatched and its next state decided.

- Each handler's side effect and its own processed_event row are written
  in a DIFFERENT session — one that InProcessTransport opens fresh per
  handler call (app.core.outbox_transport), committed inside
  consume_once() (app.core.consumer). That commit happens independently
  of, and before, poll_once()'s own commit.

- These two transactions must NEVER become one. If a consumer's side
  effect and its processed_event row were ever written as part of THIS
  module's claim transaction, handler success and the outbox status
  update would become atomic with each other. That sounds desirable and
  is exactly wrong: a crash between "handler ran" and "outbox row marked
  published" must leave the message PENDING, so it gets redelivered — not
  silently vanish as attempted-but-unrecorded. At-least-once delivery
  depends on these staying two separate commits, on purpose.

- What happens if a consumer's transaction fails: InProcessTransport
  catches the exception, closes that handler's session — rolling back its
  partial work — and reports it as one failure inside a DeliveryError.
  poll_once() sees the DeliveryError and decides retry-with-backoff or
  dead-letter for the WHOLE message, writing that decision into its own
  (separate) claim transaction. A sibling consumer that already succeeded
  on the same message is not undone — it committed independently, and
  consume_once()'s idempotency check (processed_event already has that
  (event_id, consumer_name) pair) means it will not run again on retry.
  Only the consumer(s) that actually failed re-run.
"""

import dataclasses
import datetime as dt
import logging
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import DeliveryError, EventTransport

logger = logging.getLogger("app.outbox")

MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 1
_BACKOFF_CAP_SECONDS = 3600
_LAST_ERROR_MAX_LENGTH = 2000


def compute_backoff(attempts: int) -> dt.timedelta:
    """Exponential backoff with jitter, capped at ~1 hour (Decision 5).
    Pure function — no I/O, no clock reads beyond what the caller passes
    in via `attempts` — so the growth curve and cap are unit-testable
    directly without a database.
    """

    exponential = _BACKOFF_BASE_SECONDS * (2**attempts)
    capped = min(exponential, _BACKOFF_CAP_SECONDS)
    jitter = capped * random.uniform(0, 0.2)
    return dt.timedelta(seconds=capped + jitter)


@dataclasses.dataclass(frozen=True)
class PollResult:
    claimed: int
    published: int
    retried: int
    dead: int


def poll_once(db: Session, transport: EventTransport, *, batch_size: int = 100) -> PollResult:
    """One claim-dispatch-commit cycle. See the module docstring for the
    session-boundary rules this function depends on for correctness.
    """

    stmt = (
        select(OutboxMessage)
        .where(OutboxMessage.status == OutboxStatus.PENDING, OutboxMessage.next_attempt_at <= utcnow())
        .order_by(OutboxMessage.id)  # UUIDv7 is time-ordered — this is the per-aggregate ordering guarantee,
        # and it holds only with a single worker. A second worker means ordering must be enforced
        # per aggregate_id instead (e.g. claim grouped/serialized by aggregate_id), not just by id.
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    claimed = list(db.scalars(stmt).all())

    published = retried = dead = 0
    for message in claimed:
        try:
            transport.deliver(message)
        except DeliveryError as exc:
            message.attempts += 1
            message.last_error = str(exc)[:_LAST_ERROR_MAX_LENGTH]
            if message.attempts >= MAX_ATTEMPTS:
                message.status = OutboxStatus.DEAD
                dead += 1
                logger.error(
                    "outbox message dead-lettered",
                    extra={
                        "outboxMessageId": str(message.id),
                        "eventType": message.event_type,
                        "attempts": message.attempts,
                        "lastError": message.last_error,
                    },
                )
            else:
                message.next_attempt_at = utcnow() + compute_backoff(message.attempts)
                retried += 1
        else:
            message.status = OutboxStatus.PUBLISHED
            message.published_at = utcnow()
            published += 1

    db.commit()
    return PollResult(claimed=len(claimed), published=published, retried=retried, dead=dead)
