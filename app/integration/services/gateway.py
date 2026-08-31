"""The provider-gateway itself (WP-6 PR-2/3) — resolves which adapter a
connection speaks through, times and logs every call, and (PR-3) wraps
credential resolution and resilience around it. Callers (app/vehicle/
services/catalogue_sync.py, PR-4) never touch an adapter class directly;
they go through `call_capability`, so mock and real providers are
interchangeable by nothing more than which `provider_code` a connection
points at.

"No business data" (Integrations & API Credentials v0.1's own words) —
this module writes exactly one thing, `integration_call_log`, and reads
nothing but `integration_connection`/`integration_provider`.
"""

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
from app.integration.models.connection import ConnectionStatus, IntegrationConnection
from app.integration.services import providers as provider_service
from app.integration.services import resilience


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
) -> Iterator[ProviderAdapter]:
    """Yields the resolved adapter, timing the caller's own use of it and
    writing exactly one `integration_call_log` row regardless of outcome.
    A disabled connection or an open circuit breaker refuses BEFORE
    resolving an adapter or writing a log row at all — "no business data"
    extends to not logging a call that never reached the provider.
    `actor_id`/`purpose` are threaded through to the adapter factory
    purely so a real adapter (PR-3) can audit-log its own credential
    resolutions; the mock adapter ignores both.

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
        record_call(
            db, connection=connection, capability=capability, status=CallStatus.ERROR,
            duration_ms=duration_ms, correlation_id=correlation_id,
        )
        raise
    else:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        resilience.record_success(connection.id)
        record_call(
            db, connection=connection, capability=capability, status=CallStatus.SUCCESS,
            duration_ms=duration_ms, correlation_id=correlation_id,
        )


def test_connection(db: Session, *, connection: IntegrationConnection) -> IntegrationConnection:
    """The dealer-facing "Test connection" action (PR-7's own UI) — a
    lightweight probe: fetch the system watermark, which every provider
    account can read regardless of its other entitlements. Updates
    status/last_verified_at/last_error directly; probing per-capability
    entitlements (images/packages/valuation/forecast) is PR-5's job.
    """

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
    db.commit()
    db.refresh(connection)
    return connection
