"""Thin transport seam (PR-4, Decision 7, ADR-006). One interface, one
implementation — a Kafka-compatible adapter is a future second
implementation of EventTransport, not built here. Do not add a plugin
system for an implementation that doesn't exist yet.

Session ownership: InProcessTransport.deliver() opens a NEW session per
registered handler, via its own session_factory — never the session the
worker used to claim the batch (see app.core.outbox_worker's module
docstring). Each handler's session is independent of every other
handler's and of the worker's claim session; a handler's transaction
commits or rolls back entirely on its own.
"""

import dataclasses
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.consumer import Handler, consume_once
from app.core.outbox_model import OutboxMessage


class EventTransport(Protocol):
    def deliver(self, message: OutboxMessage) -> None:
        """Delivers one message to every registered consumer. Raises
        DeliveryError if any consumer failed — the worker (not this
        interface) decides what that means for retry/backoff/dead-letter.
        Consumers that already succeeded are not undone by another
        consumer's failure; each committed independently.
        """
        ...


@dataclasses.dataclass
class DeliveryError(Exception):
    """Raised by InProcessTransport.deliver() when one or more consumers
    failed. Carries every failure, not just the first — the worker logs
    and retries based on this, and a later attempt only re-runs the
    consumers that are still missing a processed_event row (see
    consume_once's idempotency check).
    """

    message: OutboxMessage
    failures: list[tuple[str, Exception]]

    def __str__(self) -> str:
        details = "; ".join(f"{name}: {exc}" for name, exc in self.failures)
        return f"{len(self.failures)} consumer(s) failed for message {self.message.id} — {details}"


class InProcessTransport:
    """Today's EventTransport: dispatches straight to in-process handler
    functions instead of a broker. Handler registry starts empty in this
    PR — no real business events or handlers ship here, only the
    test-only probe used to prove the mechanism (see tests/).
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._handlers: dict[str, list[tuple[str, Handler]]] = {}

    def register(self, event_type: str, *, consumer_name: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append((consumer_name, handler))

    def registered_consumer_names(self) -> set[str]:
        """Every distinct consumer_name registered against any event type —
        WP-2 PR-3's consumer-lag alarm needs this to know which consumers
        to check app.core.outbox.consumer_lag_seconds() for; nothing else
        in this module needs the registry exposed.
        """

        return {consumer_name for handlers in self._handlers.values() for consumer_name, _ in handlers}

    def deliver(self, message: OutboxMessage) -> None:
        failures: list[tuple[str, Exception]] = []
        for consumer_name, handler in self._handlers.get(message.event_type, []):
            db = self._session_factory()
            try:
                consume_once(db, message=message, consumer_name=consumer_name, handler=handler)
            except Exception as exc:  # noqa: BLE001 — collected, not swallowed; see DeliveryError
                failures.append((consumer_name, exc))
            finally:
                db.close()
        if failures:
            raise DeliveryError(message, failures)
