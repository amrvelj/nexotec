"""One `integration_call_log` row per provider request (KAN-486).

`gateway.call_capability` used to write one row per block, whatever the
block did — a catalogue lookup's five `Suchen` requests (`Fahrzeuge`,
`Optionen`, `OptionenFarben`, `PneuDimTS`, `Bilder`) were one `vehicle_data`
row, and a request `call_with_retry` sent twice was one row too. The provider
counts requests, not blocks (the identAPI test agreement caps them, §6), so
the log now does the same: an adapter wraps every request it sends in
`provider_request(<the provider's own operation name>)`, and each attempt it
sends inside that — the retry included — is a row of its own, with the
operation name as `capability` and `cost_units = 1` unless the provider bills
otherwise.

The block is still the unit of the circuit breaker (charged or reset once per
`call_capability`, as before) and still writes its single row when no request
was metered inside it: a login that failed before the request, an adapter
with nothing to send. Nothing here enforces a limit: auto-i-dat enforces its own (Anto,
2026-10-29, KAN-486); this only counts honestly.

The meter is a `ContextVar` that `call_capability` sets for the length of its
block, so adapters need no gateway import and no extra argument. An adapter
used outside a block (an adapter unit test) meters nothing.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from app.integration.models.call_log import CallStatus, IntegrationCallLog

T = TypeVar("T")

# (operation, status, duration_ms, cost_units) -> the row written
RecordRequest = Callable[[str, CallStatus, int, Decimal | None], IntegrationCallLog]

ONE_REQUEST = Decimal(1)


@dataclass
class CallMeter:
    """The block's tally. `requests` counts every attempt recorded;
    `last_log` is the newest row, the one a captured payload belongs to."""

    record: RecordRequest
    requests: int = 0
    last_log: IntegrationCallLog | None = None

    def write(self, operation: str, status: CallStatus, started_at: float, cost_units: Decimal | None) -> None:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        self.last_log = self.record(operation, status, duration_ms, cost_units)
        self.requests += 1


_CURRENT: ContextVar[CallMeter | None] = ContextVar("integration_call_meter", default=None)


@contextmanager
def metering(record: RecordRequest) -> Iterator[CallMeter]:
    """`call_capability`'s side: the meter its block's requests are counted on."""

    meter = CallMeter(record=record)
    token = _CURRENT.set(meter)
    try:
        yield meter
    finally:
        _CURRENT.reset(token)


def _never_a_timeout(exc: BaseException) -> bool:
    return False


class ProviderRequest:
    """One request as the provider sees it. `attempt(send)` sends it once:
    a failed attempt is written at once (`TIMEOUT` when `is_timeout` says so,
    else `ERROR`) and re-raised, so a retry around `attempt` is a second row;
    a successful attempt is written when the request ends — `SUCCESS`, or
    `ERROR` when what came back is refused afterwards (an empty answer, a
    response that will not decrypt or parse)."""

    def __init__(
        self, meter: CallMeter | None, operation: str, *, cost_units: Decimal | None,
        is_timeout: Callable[[BaseException], bool],
    ) -> None:
        self._meter = meter
        self._operation = operation
        self._cost_units = cost_units
        self._is_timeout = is_timeout
        self._answered_at: float | None = None

    def attempt(self, send: Callable[[], T]) -> T:
        started_at = time.monotonic()
        try:
            result = send()
        except Exception as exc:
            if self._meter is not None:
                status = CallStatus.TIMEOUT if self._is_timeout(exc) else CallStatus.ERROR
                self._meter.write(self._operation, status, started_at, self._cost_units)
            raise
        self._answered_at = started_at
        return result

    def _end(self, *, failed: bool) -> None:
        if self._meter is None or self._answered_at is None:
            return
        status = CallStatus.ERROR if failed else CallStatus.SUCCESS
        self._meter.write(self._operation, status, self._answered_at, self._cost_units)
        self._answered_at = None


@contextmanager
def provider_request(
    operation: str, *, cost_units: Decimal | None = ONE_REQUEST,
    is_timeout: Callable[[BaseException], bool] = _never_a_timeout,
) -> Iterator[ProviderRequest]:
    """The adapter's side: wraps everything from sending the request to
    accepting what came back, so a refused answer is the request's own
    `ERROR`. `operation` is the provider's own name for it (the identAPI
    operation, `FtpUpload` for a marketplace feed)."""

    request = ProviderRequest(_CURRENT.get(), operation, cost_units=cost_units, is_timeout=is_timeout)
    try:
        yield request
    except BaseException:
        request._end(failed=True)
        raise
    request._end(failed=False)
