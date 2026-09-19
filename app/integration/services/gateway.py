"""The provider-gateway itself (WP-6 PR-2/3) — resolves which adapter a
connection speaks through, times and logs every call, and (PR-3) wraps
credential resolution and resilience around it. Callers (app/vehicle/
services/catalogue_sync.py, PR-4) never touch an adapter class directly;
they go through `call_capability`, so mock and real providers are
interchangeable by nothing more than which `provider_code` a connection
points at.

"No business data" (Integrations & API Credentials v0.1's own words) —
this module writes `integration_call_log`, and reads nothing but
`integration_connection`/`integration_provider`. The one other thing it
writes is `integration_entitlement`, and only from `test_connection`'s
probes (KAN-38 PR 2b) — registry metadata about the connection itself, not
business data. PR-6 adds
one narrow exception: `call_capability`'s optional `capture_raw_payload`
callable, which — if a caller supplies one — writes a SECOND row to the
structurally separate `integration_call_payload` table (never this
module's own `integration_call_log`). No adapter in this codebase
supplies one today (see `services/retention.py`'s own docstring); this
is the seam a future adapter revision uses, not a currently-exercised
path.
"""

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.base import ProviderAdapter
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.call_payload import PayloadKind
from app.integration.models.connection import ConnectionStatus, IntegrationConnection
from app.integration.services import connections as connection_service
from app.integration.services import entitlement_probes, resilience
from app.integration.services import providers as provider_service
from app.integration.services import retention as retention_service

logger = logging.getLogger("app.integration.gateway")


class ProviderGatewayError(Exception):
    """Base for every gateway-level failure — never a bare exception
    reaching a caller outside this context (PR-3's resilience layer wraps
    this further with timeout/retry/circuit-breaker specifics).
    """


class ConnectionDisabledError(ProviderGatewayError):
    def __init__(self, connection_id: uuid.UUID) -> None:
        super().__init__(f"Connection {connection_id} is disabled.")


class UnknownProviderError(ProviderGatewayError):
    def __init__(self, provider_code: str) -> None:
        super().__init__(f"No adapter is registered for provider '{provider_code}'.")


# provider_code -> factory(db, connection, actor_id, purpose) -> adapter
# instance. The db/actor_id/purpose triple exists purely so a real adapter
# (PR-3's AutoIDatSoapAdapter) can audit-log each credential resolution
# with "actor, tenant, connection, purpose" (rule 4) — the mock factory
# below ignores all three. Mock and real are separate provider_codes /
# separate connections, never a runtime flag on one (rule 7's own
# reasoning, applied to more than just sandbox/prod).
_ADAPTER_FACTORIES: dict[str, Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter]] = {
    "auto_i_dat_mock": lambda db, connection, actor_id, purpose: MockAutoIDatAdapter(),
}


def register_adapter_factory(
    provider_code: str, factory: Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter]
) -> None:
    """PR-3 registers the real SOAP adapter here rather than editing this
    module's own dict in place — keeps the two adapters' modules
    independent of each other (neither imports the other).
    """

    _ADAPTER_FACTORIES[provider_code] = factory


def _resolve_adapter(
    db: Session, connection: IntegrationConnection, *, actor_id: uuid.UUID | None, purpose: str
) -> ProviderAdapter:
    if not connection.enabled:
        raise ConnectionDisabledError(connection.id)
    if resilience.is_circuit_open(connection.id):
        raise resilience.CircuitOpenError(connection.id)
    provider = provider_service.get_provider_or_404(db, connection.provider_id)
    factory = _ADAPTER_FACTORIES.get(provider.provider_code)
    if factory is None:
        raise UnknownProviderError(provider.provider_code)
    return factory(db, connection, actor_id, purpose)


def resolve_adapter(
    db: Session, connection: IntegrationConnection, *, actor_id: uuid.UUID | None = None, purpose: str = ""
) -> ProviderAdapter:
    """The same resolution `call_capability` does (enabled check, circuit
    check, factory lookup) but WITHOUT any call-log row or circuit-
    breaker recording — for a caller that needs the adapter object for a
    purely local operation that makes no provider I/O (KAN-27's
    `MarketplaceAdapter.validate_listing`, a pure data-shape check).

    A real provider call must never use this — `call_capability` is the
    only place a call that actually reaches the provider is allowed to
    happen, precisely so every such call is logged and every failure
    counted toward that connection's circuit breaker. Resolving an
    adapter here to then quietly do I/O with it would defeat both.
    """

    return _resolve_adapter(db, connection, actor_id=actor_id, purpose=purpose)


def record_call(
    db: Session,
    *,
    connection: IntegrationConnection,
    capability: str,
    status: CallStatus,
    duration_ms: int,
    cost_units: Decimal | None = None,
    correlation_id: uuid.UUID | None = None,
) -> IntegrationCallLog:
    log = IntegrationCallLog(
        connection_id=connection.id,
        tenant_id=connection.tenant_id,
        capability=capability,
        status=status,
        duration_ms=duration_ms,
        cost_units=cost_units,
        correlation_id=correlation_id or uuid.uuid4(),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@contextmanager
def call_capability(
    db: Session,
    *,
    connection: IntegrationConnection,
    capability: str,
    correlation_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    purpose: str = "",
    capture_raw_payload: Callable[[], str | None] | None = None,
) -> Iterator[ProviderAdapter]:
    """Yields the resolved adapter, timing the caller's own use of it and
    writing exactly one `integration_call_log` row regardless of outcome.
    A disabled connection or an open circuit breaker refuses BEFORE
    resolving an adapter or writing a log row at all — "no business data"
    extends to not logging a call that never reached the provider.
    `actor_id`/`purpose` are threaded through to the adapter factory
    purely so a real adapter (PR-3) can audit-log its own credential
    resolutions; the mock adapter ignores both.

    `capture_raw_payload` (PR-6) is called once, after the caller's own
    block finishes (success or error), and — if it returns a non-None
    string — writes ONE `integration_call_payload` row, structurally
    separate from `integration_call_log`. No adapter in this codebase
    supplies this today (see services/retention.py's own docstring); it
    exists so a future adapter revision can start capturing a real wire
    payload without any change to this function's own callers.

    Usage:
        with call_capability(db, connection=connection, capability="vehicle_data") as adapter:
            data = adapter.fetch_vehicle_master_data(fz_key)
    """

    adapter = _resolve_adapter(db, connection, actor_id=actor_id, purpose=purpose or capability)
    started_at = time.monotonic()
    try:
        yield adapter
    except Exception:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        resilience.record_failure(connection.id)
        log = record_call(
            db, connection=connection, capability=capability, status=CallStatus.ERROR,
            duration_ms=duration_ms, correlation_id=correlation_id,
        )
        _maybe_capture_payload(db, log=log, kind=PayloadKind.ERROR, capture_raw_payload=capture_raw_payload)
        raise
    else:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        resilience.record_success(connection.id)
        log = record_call(
            db, connection=connection, capability=capability, status=CallStatus.SUCCESS,
            duration_ms=duration_ms, correlation_id=correlation_id,
        )
        _maybe_capture_payload(db, log=log, kind=PayloadKind.SUCCESS, capture_raw_payload=capture_raw_payload)


def _maybe_capture_payload(
    db: Session, *, log: IntegrationCallLog, kind: PayloadKind, capture_raw_payload: Callable[[], str | None] | None
) -> None:
    if capture_raw_payload is None:
        return
    payload = capture_raw_payload()
    if payload is not None:
        retention_service.capture_call_payload(db, call_log=log, kind=kind, payload=payload)
        db.commit()


def test_connection(db: Session, *, connection: IntegrationConnection) -> IntegrationConnection:
    """The dealer-facing "Test connection" action (PR-7's own UI) — a
    lightweight probe: fetch the system watermark, which every provider
    account can read regardless of its other entitlements. Updates
    status/last_verified_at/last_error directly.

    KAN-38 PR 2b: once the watermark call has succeeded, also probes the
    capabilities `services/entitlement_probes.py` can probe honestly and
    records each result as an `integration_entitlement` row. The ordering is
    load-bearing — a probe's rejection is only attributable to the
    capability because `System` has just accepted the same credentials — so
    a connection that fails here is never probed. Each probe is a real,
    logged, potentially billed provider call: one click is now two calls
    for `auto_i_dat`, not one.
    """

    healthy = False
    try:
        with call_capability(db, connection=connection, capability="system_watermark") as adapter:
            adapter.get_system_watermark()
    except ProviderGatewayError as exc:
        connection.status = ConnectionStatus.ERROR
        connection.last_error = str(exc)
    else:
        connection.status = ConnectionStatus.CONNECTED
        connection.last_verified_at = utcnow()
        connection.last_error = None
        healthy = True
    db.commit()
    if healthy:
        _probe_entitlements(db, connection=connection)
    db.refresh(connection)
    return connection


def _probe_entitlements(db: Session, *, connection: IntegrationConnection) -> None:
    """A refusal is caught *inside* the `call_capability` block, so the
    gateway records it as a successful call and the connection's circuit
    breaker is not charged: the provider answered coherently, and what it
    said was "not this capability". That is the per-capability degradation
    the per-connection breaker would otherwise defeat — one un-entitled
    capability must not take the connection's other capabilities offline.
    (Within `test_connection` alone the breaker could not accumulate anyway,
    since each click's successful `System` call resets it; the rule matters
    for a probe run outside that reset.)

    Anything other than a refusal (maintenance, transport, a bug) leaves the
    existing entitlement row exactly as it was: an outage says nothing about
    what the account is entitled to, and a probe failing must never fail
    "Test connection" itself.
    """

    provider = provider_service.get_provider_or_404(db, connection.provider_id)
    for probe in entitlement_probes.probes_for(provider.provider_code):
        try:
            with call_capability(
                db,
                connection=connection,
                capability=probe.capability_code,
                purpose=f"entitlement_probe:{probe.capability_code}",
            ) as adapter:
                granted = entitlement_probes.run_probe(probe, adapter)
        except Exception:
            logger.warning(
                "entitlement probe for %s failed on connection %s; entitlement left unchanged",
                probe.capability_code, connection.id, exc_info=True,
            )
            continue
        connection_service.record_probed_entitlement(
            db, connection_id=connection.id, capability_code=probe.capability_code, granted=granted
        )
