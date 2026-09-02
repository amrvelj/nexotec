"""Timeout + one retry with jitter + a per-connection circuit breaker
(WP-6 PR-3). "A dependency being down degrades a screen; it never
cascades." Hand-rolled, not a pulled-in library — matches this codebase's
own preference for small, owned mechanisms over a dependency for
something this size (the outbox itself is hand-rolled the same way).

Split by where each concern actually applies: the circuit breaker lives
at the gateway level (services/gateway.py's own `call_capability`) since
it tracks a connection's overall health across every capability call: one
connection's outage never degrades another's (state is keyed per
connection, never global). Timeout-with-one-retry-with-jitter lives
inside each adapter's own SOAP-call helper instead (only a real network
call can transiently fail and benefit from a retry — the mock adapter
never needs one, and retrying a caller's own multi-step code block inside
a `with call_capability(...)` would double-count any side effect on a
partial retry, which is exactly the bug this split avoids).
"""

import random
import time
import uuid
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_FAILURE_THRESHOLD = 5
_OPEN_DURATION_SECONDS = 60.0
_JITTER_RANGE_SECONDS = (0.1, 0.5)
_DEFAULT_TIMEOUT_SECONDS = 10.0


class CircuitOpenError(Exception):
    def __init__(self, connection_id: uuid.UUID) -> None:
        super().__init__(f"Circuit breaker is open for connection {connection_id} — too many recent failures.")


class _CircuitState:
    __slots__ = ("failure_count", "opened_at")

    def __init__(self) -> None:
        self.failure_count = 0
        self.opened_at: float | None = None


# Keyed per connection_id — one dealer's outage never opens another
# dealer's circuit, even against the same provider.
_CIRCUITS: dict[uuid.UUID, _CircuitState] = {}


def _circuit_for(connection_id: uuid.UUID) -> _CircuitState:
    return _CIRCUITS.setdefault(connection_id, _CircuitState())


def is_circuit_open(connection_id: uuid.UUID) -> bool:
    circuit = _circuit_for(connection_id)
    if circuit.opened_at is None:
        return False
    if time.monotonic() - circuit.opened_at >= _OPEN_DURATION_SECONDS:
        # Half-open: let the next call through to test recovery, rather
        # than staying open forever once the dependency recovers.
        circuit.opened_at = None
        circuit.failure_count = 0
        return False
    return True


def record_success(connection_id: uuid.UUID) -> None:
    circuit = _circuit_for(connection_id)
    circuit.failure_count = 0
    circuit.opened_at = None


def record_failure(connection_id: uuid.UUID) -> None:
    circuit = _circuit_for(connection_id)
    circuit.failure_count += 1
    if circuit.failure_count >= _FAILURE_THRESHOLD and circuit.opened_at is None:
        circuit.opened_at = time.monotonic()


def reset_circuit(connection_id: uuid.UUID) -> None:
    """Test-only — the module-level dict otherwise carries state between
    tests that happen to construct connections (they don't share UUIDs in
    practice, but tests should never depend on that).
    """

    _CIRCUITS.pop(connection_id, None)


def call_with_retry(fn: Callable[[], T], *, max_retries: int = 1) -> T:
    """One retry, with jitter, never more — "one retry with jitter,
    circuit breaker" is the brief's own phrasing, not "retry until it
    works." Raises the last attempt's own exception if every attempt
    fails.
    """

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised verbatim below, never swallowed
            last_exc = exc
            if attempt < max_retries:
                time.sleep(random.uniform(*_JITTER_RANGE_SECONDS))
    assert last_exc is not None
    raise last_exc
