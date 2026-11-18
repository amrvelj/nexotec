"""The correlation context (KAN-125): which request or event the work in hand belongs to, and
which event caused it — the envelope's `correlationId` and `causationId`.

Three places set it, and each resets it on the way out, so it never leaks into the next piece
of work the same worker or thread handles:

- `app.core.observability.ObservabilityMiddleware`, per HTTP request, from `X-Correlation-Id`
  (a value that is not a UUID is replaced by a fresh id, never stored) or a fresh id;
- `app.core.consumer.consume_once`, while a handler runs: the consumed message's
  `correlation_id`, and its `id` as the cause of whatever the handler emits;
- `app.core.daily_scheduler.run_due_daily_jobs`, a fresh id per job run.

`app.core.outbox.publish` defaults an event's two ids from it, and every JSON log line carries
the correlation id (`app.core.observability.JSONLogFormatter`). Contextvars rather than
parameters: a service function deep in a request or a handler publishes and logs with no access
to either. Outside all three (a script, a test) nothing is set and each event gets its own id.
"""

import contextlib
import contextvars
import uuid
from collections.abc import Iterator

from app.core.uuid7 import uuid7

# Held as the canonical string: it is what log lines and the response header carry. Only
# correlation_scope() sets it, always to a UUID.
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
_causation_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar("causation_id", default=None)


def get_correlation_id() -> str | None:
    """The current correlation id as a string, None outside a request, a handler or a job run."""

    return _correlation_id.get()


def current_correlation_id() -> uuid.UUID | None:
    value = _correlation_id.get()
    return uuid.UUID(value) if value is not None else None


def current_causation_id() -> uuid.UUID | None:
    """The id of the event whose handler is running, None anywhere else."""

    return _causation_id.get()


def correlation_id_from_header(value: str | None) -> uuid.UUID:
    """A caller's `X-Correlation-Id` when it is a UUID, else a fresh id: the header is caller
    input, and only an id the outbox can store and a log line can carry safely is kept."""

    if value:
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    return uuid7()


@contextlib.contextmanager
def correlation_scope(correlation_id: uuid.UUID | None = None, *, causation_id: uuid.UUID | None = None) -> Iterator[uuid.UUID]:
    """Sets the context for the block — `correlation_id`, or a fresh one — and restores what was
    there before on the way out, however the block ends. Yields the correlation id in force."""

    in_force = correlation_id or uuid7()
    correlation_token = _correlation_id.set(str(in_force))
    causation_token = _causation_id.set(causation_id)
    try:
        yield in_force
    finally:
        _causation_id.reset(causation_token)
        _correlation_id.reset(correlation_token)
