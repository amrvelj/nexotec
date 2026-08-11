"""Consumer harness (PR-4, Decision 6). One function, one rule: a
consumer's side effect and its processed_event row are written in the
SAME transaction. If those two can diverge, the consumer is not
idempotent regardless of what the processed_event table says — so this
harness is the only place that's allowed to write either.

Session ownership: consume_once() is always called with a session that
belongs to the CONSUMER, never the outbox worker's own claim-transaction
session (see app.core.outbox_worker's module docstring for the full
boundary). This function commits that session itself — the caller
(app.core.outbox_transport.InProcessTransport) does not commit around it.
"""

from typing import Callable

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent

Handler = Callable[[Session, OutboxMessage], None]


def consume_once(db: Session, *, message: OutboxMessage, consumer_name: str, handler: Handler) -> bool:
    """Runs `handler` exactly once per (message.id, consumer_name), ever.
    Returns True if the handler ran (first delivery), False if
    processed_event already had this pair (idempotent no-op on redelivery
    — the handler is not called again). Commits on success; on a handler
    exception, the caller's session is left uncommitted with the handler's
    partial work rolled back by the caller (see InProcessTransport).
    """

    existing = db.get(ProcessedEvent, (message.id, consumer_name))
    if existing is not None:
        return False

    handler(db, message)
    db.add(ProcessedEvent(event_id=message.id, consumer_name=consumer_name, processed_at=utcnow()))
    db.commit()
    return True
