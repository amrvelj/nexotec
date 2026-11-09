"""The provider-gateway itself (WP-6 PR-2/3) — resolves which adapter a
connection speaks through, times and logs every call, and (PR-3) wraps
credential resolution and resilience around it. Callers (app/vehicle/
services/catalogue_sync.py, PR-4) never touch an adapter class directly;
they go through `call_capability`, so mock and real providers are
interchangeable by nothing more than which `provider_code` a connection
points at.

"No business data" (Integrations & API Credentials v0.1's own words) —
this module writes `integration_call_log` (through `call_capability`'s
own session, never the caller's — ADR-047, KAN-90), and reads nothing but
`integration_connection`/`integration_provider`. The other things it
writes are `integration_entitlement`, only from `test_connection`'s probes
(KAN-38 PR 2b) — registry metadata about the connection itself, not
business data — and an `audit_event` when it refuses a sandbox connection
on a production deployment (KAN-489). PR-6 adds
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

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.base import ProviderAdapter
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.call_payload import PayloadKind
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus, IntegrationConnection
from app.integration.services import captions as captions_service
from app.integration.services import connections as connection_service
from app.integration.services import entitlement_probes, metering, resilience
from app.integration.services import providers as provider_service
from app.integration.services import retention as retention_service

logger = logging.getLogger("app.integration.gateway")

_AUDIT_ENTITY_TYPE = "integration_connection"
# KAN-489: the deployments on which a sandbox (test access) connection may run.
_NON_PRODUCTION_DEPLOYMENTS = frozenset({"development", "local", "test", "staging"})


# `ProviderGatewayError` itself lives in `app.integration.errors` (a leaf, so
# adapters can subclass it too) and is still importable from here.


class ConnectionDisabledError(ProviderGatewayError):
    def __init__(self, connection_id: uuid.UUID) -> None:
        super().__init__(f"Connection {connection_id} is disabled.")


class SandboxConnectionOnProductionError(ProviderGatewayError):
    """KAN-489: a connection whose environment is `sandbox` is a test access
    — for auto-i-dat, the identAPI test agreement, whose service account may
    run in test environments only, never in production (§1). On a
    deployment whose `DMS_ENVIRONMENT` is not a known non-production name
    (`_NON_PRODUCTION_DEPLOYMENTS`) every call on it is refused before the
    provider is reached, with an audit row; "Test connection" turns the
    card's badge to error like any other failure."""

    def __init__(self, connection_id: uuid.UUID) -> None:
        super().__init__(
            f"Connection {connection_id} is a sandbox (test access) connection; "
            "it is never used on the production server."
        )


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
_AdapterFactory = Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter]

_ADAPTER_FACTORIES: dict[str, _AdapterFactory] = {
    "auto_i_dat_mock": lambda db, connection, actor_id, purpose: MockAutoIDatAdapter(),
}


def register_adapter_factory(provider_code: str, factory: _AdapterFactory) -> None:
    """PR-3 registers the real SOAP adapter here rather than editing this
    module's own dict in place — keeps the two adapters' modules
    independent of each other (neither imports the other).
    """

    _ADAPTER_FACTORIES[provider_code] = factory


def _adapter_factory(db: Session, connection: IntegrationConnection) -> _AdapterFactory:
    """Every refusal that comes before the provider is reached — a disabled
    connection, an open circuit, a provider with no adapter. Reads only; no
    provider I/O. Calling the factory it returns is where a real adapter
    may make its first network call (the auto-i-dat WSDL fetch)."""

    if not connection.enabled:
        raise ConnectionDisabledError(connection.id)
    if resilience.is_circuit_open(connection.id):
        raise resilience.CircuitOpenError(connection.id)
    provider = provider_service.get_provider_or_404(db, connection.provider_id)
    factory = _ADAPTER_FACTORIES.get(provider.provider_code)
    if factory is None:
        raise UnknownProviderError(provider.provider_code)
    return factory


def _refuse_sandbox_on_production(
    gateway_db: Session, connection: IntegrationConnection, *, actor_id: uuid.UUID | None, purpose: str
) -> None:
    """KAN-489 (`SandboxConnectionOnProductionError`). The audit row is
    written and committed on the gateway's own session, never the caller's
    (KAN-90): the refusal is a fact whether or not the caller's work commits."""

    if connection.environment != ConnectionEnvironment.SANDBOX:
        return
    # Fails closed: any name but a known non-production one ("prod",
    # "Production", a typo) counts as production. An UNSET DMS_ENVIRONMENT
    # reads as "development" (app/core/config.py) and is not caught here —
    # the production deployment must set it (render.yaml).
    if get_settings().environment.strip().lower() in _NON_PRODUCTION_DEPLOYMENTS:
        return
    record_audit_event(
        gateway_db, entity_type=_AUDIT_ENTITY_TYPE, entity_id=connection.id, tenant_id=connection.tenant_id,
        action="sandbox_refused_in_production", actor_id=actor_id,
        after={"environment": connection.environment.value, "purpose": purpose},
    )
    gateway_db.commit()
    logger.warning("sandbox connection %s refused on a production deployment", connection.id)
    raise SandboxConnectionOnProductionError(connection.id)


def _resolve_adapter(
    db: Session, connection: IntegrationConnection, *, actor_id: uuid.UUID | None, purpose: str
) -> ProviderAdapter:
    return _adapter_factory(db, connection)(db, connection, actor_id, purpose)


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
    happen, precisely so every such call is logged and every provider
    failure counted toward that connection's circuit breaker. Resolving an
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
        # The name at the time of the call, never refreshed (KAN-470).
        connection_label=connection.display_name,
        connection_label_refreshed_at=utcnow(),
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
    writing an `integration_call_log` row for every provider request the
    block sends (KAN-486, `services/metering.py`) — or, when the adapter
    metered none, the block's one row, as before KAN-486.
    A disabled connection, an open circuit breaker or a sandbox connection
    on a production deployment (KAN-489; that one writes an audit row)
    refuses BEFORE resolving an adapter or writing a log row at all — "no business data"
    extends to not logging a call that never reached the provider.
    Building the adapter is part of the call, not of the refusal: a provider
    I/O failure while building it (the auto-i-dat WSDL fetch) is logged and
    charged to the breaker like a failure in the caller's block (KAN-108),
    and the logged duration includes it. `actor_id`/`purpose` are threaded through to the adapter factory
    purely so a real adapter (PR-3) can audit-log its own credential
    resolutions; the mock adapter ignores both.

    `capture_raw_payload` (PR-6) is called once, after the caller's own
    block finishes (success or error), and — if it returns a non-None
    string — writes ONE `integration_call_payload` row, structurally
    separate from `integration_call_log`. No adapter in this codebase
    supplies this today (see services/retention.py's own docstring); it
    exists so a future adapter revision can start capturing a real wire
    payload without any change to this function's own callers.

    Every row this function causes — the call log, a captured payload,
    and whatever the adapter records itself (the real auto-i-dat adapter
    audit-logs each credential resolution) — is written through a session
    of the gateway's OWN on the caller's engine, and committed there
    whether the caller's block succeeds or raises. The caller's session
    is never written to or committed here, so whatever the caller writes
    inside the `with` block is committed or rolled back by the caller
    alone (ADR-047, KAN-90). Before KAN-90, `record_call` committed the
    caller's session: a catalogue sync that failed half-way saved its
    half-written catalogue rows together with the error log row.

    A caller may hold its own connection row locked: since KAN-470 the
    log row has no foreign key to it, so nothing here waits on that row.
    Any lock wait is still bounded by `_LOG_LOCK_TIMEOUT`, so a wait added
    later fails instead of hanging.

    Usage:
        with call_capability(db, connection=connection, capability="vehicle_data") as adapter:
            data = adapter.fetch_vehicle_master_data(fz_key)
    """

    # One block, one correlation: every row it writes carries the same id.
    correlation_id = correlation_id or uuid.uuid4()
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as gateway_db:
        factory = _adapter_factory(gateway_db, connection)
        _refuse_sandbox_on_production(gateway_db, connection, actor_id=actor_id, purpose=purpose or capability)
        # The provider lookup was a read: end its transaction so the
        # connection goes back to the pool for the length of the caller's
        # block — and of the adapter's own construction, which may wait on
        # the network.
        gateway_db.commit()

        def record_request(operation: str, status: CallStatus, duration_ms: int, cost_units: Decimal | None):
            _bound_lock_wait(gateway_db)
            return record_call(
                gateway_db, connection=connection, capability=operation, status=status, duration_ms=duration_ms,
                cost_units=cost_units, correlation_id=correlation_id,
            )

        started_at = time.monotonic()
        with metering.metering(record_request) as meter:
            try:
                # KAN-108: built INSIDE the accounted part of the call. For
                # `auto_i_dat` this fetches the WSDL when the connection has no
                # cached copy (KAN-213) — the first thing to hang when
                # the provider's host does — and before KAN-108 a failure here
                # escaped both the breaker and the call log. Only a transport
                # failure is accounted: a `ProviderConfigurationError` (no
                # `wsdlUrl`) is the dealer's to fix, never reached the provider,
                # and passes through uncounted and unlogged, like the refusals in
                # `_adapter_factory`.
                adapter = factory(gateway_db, connection, actor_id, purpose or capability)
            except ProviderTransportError as exc:
                _record_failed_call(
                    gateway_db, connection=connection, capability=capability, exc=exc, started_at=started_at,
                    correlation_id=correlation_id, capture_raw_payload=None, meter=meter,
                )
                raise
            try:
                yield adapter
            except ProviderConfigurationError:
                # KAN-488: the identAPI adapter resolves its credential and
                # builds its token inside the block (lazily, on the first
                # request), so a missing `clientId` or `client_secret`, or a
                # call with nobody to enrol, surfaces here rather than in the
                # factory. Same rule as there: the dealer's to fix, so the
                # breaker is never charged and no row is invented for a request
                # that was not sent. What the adapter did send before it
                # stopped (the discovery read) keeps the row it wrote, and the
                # audit rows it recorded (a secret resolution) are committed.
                _bound_lock_wait(gateway_db)
                gateway_db.commit()
                raise
            except Exception as exc:
                _record_failed_call(
                    gateway_db, connection=connection, capability=capability, exc=exc, started_at=started_at,
                    correlation_id=correlation_id, capture_raw_payload=capture_raw_payload, meter=meter,
                )
                raise
            else:
                resilience.record_success(connection.id)
                if meter.requests:
                    # KAN-486: each request is its own row already.
                    log = meter.last_log
                else:
                    duration_ms = int((time.monotonic() - started_at) * 1000)
                    _bound_lock_wait(gateway_db)
                    log = record_call(
                        gateway_db, connection=connection, capability=capability, status=CallStatus.SUCCESS,
                        duration_ms=duration_ms, correlation_id=correlation_id,
                    )
                _maybe_capture_payload(
                    gateway_db, log=log, kind=PayloadKind.SUCCESS, capture_raw_payload=capture_raw_payload
                )


def _record_failed_call(
    gateway_db: Session,
    *,
    connection: IntegrationConnection,
    capability: str,
    exc: Exception,
    started_at: float,
    correlation_id: uuid.UUID | None,
    capture_raw_payload: Callable[[], str | None] | None,
    meter: metering.CallMeter,
) -> None:
    """Charges the breaker — once per block, whatever it sent, unless the
    failure is the provider's coherent answer to our request (below) — and,
    when the block metered no request of its own (KAN-486), writes the block's
    one log row: `TIMEOUT` for a transport timeout (KAN-108), `ERROR` for
    every other failure.

    A block that did meter requests already has a row for each. When the
    newest of them still reads `SUCCESS`, the failure came after its answer
    arrived — an answer the caller refused (`System` with no row, a
    `Fahrzeuge` miss read through `.first`) or the caller's own error — and
    that row is marked as the failure, so the log never shows a failed,
    breaker-charged call as a success. No extra row: nothing more was sent."""

    # KAN-488: a 400/422 (`ProviderRequestError`: an unknown key, a malformed
    # filter) and a 403 (`ProviderNotEntitledError`: the licence lacks this
    # option) are the provider's coherent answers to our request, not a
    # failure of the provider. The request's row reads ERROR (it was refused),
    # but five such answers must not take the connection offline: a user
    # typing a wrong Stammnummer five times is not an outage, and one
    # un-entitled option must not switch off the others (the rule the probes
    # already live by). A refused login, a transport failure and a 5xx charge.
    if not isinstance(exc, (ProviderRequestError, ProviderNotEntitledError)):
        resilience.record_failure(connection.id)
    if meter.requests:
        log = meter.last_log
        if log is not None and log.status == CallStatus.SUCCESS:
            log.status = CallStatus.TIMEOUT if isinstance(exc, ProviderTimeoutError) else CallStatus.ERROR
            _bound_lock_wait(gateway_db)
            gateway_db.commit()
    else:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        status = CallStatus.TIMEOUT if isinstance(exc, ProviderTimeoutError) else CallStatus.ERROR
        _bound_lock_wait(gateway_db)
        log = record_call(
            gateway_db, connection=connection, capability=capability, status=status,
            duration_ms=duration_ms, correlation_id=correlation_id,
        )
    _maybe_capture_payload(gateway_db, log=log, kind=PayloadKind.ERROR, capture_raw_payload=capture_raw_payload)


_LOG_LOCK_TIMEOUT = "5s"


def _bound_lock_wait(db: Session) -> None:
    """Until KAN-470 the call log's foreign key took a KEY SHARE lock on
    the connection row, so a caller holding a conflicting lock on that row
    in its own open transaction made the gateway's session wait on its own
    request forever — Postgres sees two sessions, not a deadlock. The
    foreign key is gone; the bound stays, for this transaction only
    (`SET LOCAL`), so any such wait reintroduced fails instead of hanging."""

    if db.get_bind().dialect.name == "postgresql":
        db.execute(text(f"SET LOCAL lock_timeout = '{_LOG_LOCK_TIMEOUT}'"))


def _maybe_capture_payload(
    db: Session, *, log: IntegrationCallLog | None, kind: PayloadKind,
    capture_raw_payload: Callable[[], str | None] | None,
) -> None:
    if capture_raw_payload is None or log is None:
        return
    payload = capture_raw_payload()
    if payload is not None:
        retention_service.capture_call_payload(db, call_log=log, kind=kind, payload=payload)
        db.commit()


def test_connection(
    db: Session, *, connection: IntegrationConnection, actor_id: uuid.UUID | None = None
) -> IntegrationConnection:
    """The dealer-facing "Test connection" action (PR-7's own UI) — an
    access check every account of the provider can pass whatever its other
    entitlements (`entitlement_probes.access_check`: for the identAPI the
    login plus `GET /caption/code`, for the SOAP-shaped mock the `System`
    watermark), then the entitlement probes. Updates
    status/last_verified_at/last_error directly.

    Any `ProviderGatewayError` — a refusal, a rejected login, a maintenance
    window, a transport failure, an open circuit — becomes `ERROR` with the
    exception's message in `last_error`, never an HTTP 500. That holds only
    because adapters translate what their third-party client throws into that
    type (`app/integration/errors.py`); this function deliberately does NOT
    catch `Exception`, which would file a programmer error under "the
    connection failed".

    A marketplace connection has no such probe (`MarketplaceAdapter` has no
    `System` equivalent), so it is refused with a 409 before anything is
    resolved: nothing is logged, the breaker is not charged, and a healthy
    connection is not marked `ERROR` for a call that never happened.

    KAN-38 PR 2b: once the access check has succeeded, also probes the
    capabilities `services/entitlement_probes.py` can probe honestly and
    records each result as an `integration_entitlement` row. The ordering is
    load-bearing — a probe's refusal is only attributable to the capability
    because the access check has just accepted the same credentials — so a
    connection that fails here is never probed. Each probe is a real,
    logged, potentially billed provider call: for the identAPI one click is
    the login, the caption call and six probes (KAN-488).
    """

    provider = provider_service.get_provider_or_404(db, connection.provider_id)
    if provider.category == "marketplace":
        raise ConflictError(f"Connection testing is not available for {provider.display_name}.")

    # KAN-488: the identAPI's access check is the login plus
    # `GET /caption/code` (metered rows `oauth_discovery` on a cold cache,
    # `oauth_token`, `caption_code`);
    # every other vehicle-data adapter keeps the `System` watermark. The
    # actor is the user the token is requested for (auto-i's User Enrollment
    # Grant) — a test with no actor fails as a configuration error.
    check = entitlement_probes.access_check(provider.provider_code)
    healthy = False
    access_answer: object = None
    try:
        with call_capability(
            db, connection=connection, capability="system_watermark", actor_id=actor_id, purpose="test_connection"
        ) as adapter:
            access_answer = check(adapter)
    except ProviderGatewayError as exc:
        # Traceback only when there is a chained foreign cause worth reading —
        # `last_error` carries our own wording, the log carries what it hides.
        logger.warning(
            "connection test failed for connection %s: %s", connection.id, exc, exc_info=exc.__cause__ is not None
        )
        connection.status = ConnectionStatus.ERROR
        connection.last_error = str(exc)
    else:
        connection.status = ConnectionStatus.CONNECTED
        connection.last_verified_at = utcnow()
        connection.last_error = None
        healthy = True
    db.commit()
    if healthy:
        _store_captions(
            db, connection=connection, provider_code=provider.provider_code, actor_id=actor_id,
            access_answer=access_answer,
        )
        _probe_entitlements(db, connection=connection, actor_id=actor_id)
    db.refresh(connection)
    return connection


def _store_captions(
    db: Session,
    *,
    connection: IntegrationConnection,
    provider_code: str,
    actor_id: uuid.UUID | None,
    access_answer: object,
) -> None:
    """KAN-547 (ADR-111 (3)): the provider's code vocabulary in the four
    languages, stored globally (`services/captions.py`) on every successful
    «Test connection» — the German captions the access check just read
    (its json:api body, read once more here rather than requested again),
    then French, Italian and English in one block of three `caption_code`
    rows. The one place captions are refreshed: a background job has no user
    to enrol (ADR-111 as amended), and Anto wants no scheduled refresh
    (2026-11-07). A failure here is logged and leaves the stored vocabulary
    as it was; the test itself has passed and stays `connected`, and the
    entitlement probes still run. An adapter without captions (the mock
    speaks them; the SOAP shim does not) stores nothing."""

    # Imported here: the parser is the identAPI adapter's, and the gateway
    # names adapters only through their factories.
    from app.integration.adapters import identapi_parse

    if provider_code not in _ADAPTER_FACTORIES:
        return
    try:
        if isinstance(access_answer, dict):
            captions_service.store_captions(
                db, provider_code=provider_code, lang="de", captions=identapi_parse.parse_captions(access_answer)
            )
            db.commit()  # the German vocabulary is kept whatever the other languages do
            remaining = tuple(lang for lang in captions_service.CAPTION_LANGUAGES if lang != "de")
        else:
            remaining = captions_service.CAPTION_LANGUAGES
        with call_capability(
            db, connection=connection, capability="caption_code", actor_id=actor_id, purpose="captions"
        ) as adapter:
            fetch = getattr(adapter, "fetch_captions", None)
            if fetch is None:
                db.rollback()
                return
            for lang in remaining:
                captions_service.store_captions(db, provider_code=provider_code, lang=lang, captions=fetch(lang))
                db.commit()  # each language kept as it lands; a later failure loses only the rest
    except ProviderGatewayError:
        logger.warning("captions could not be refreshed for connection %s", connection.id, exc_info=True)
        db.rollback()


def _probe_entitlements(
    db: Session, *, connection: IntegrationConnection, actor_id: uuid.UUID | None = None
) -> None:
    """A refusal is caught *inside* the `call_capability` block, so the
    block succeeds and the connection's circuit breaker is not charged; the
    refused request itself is logged as `ERROR` under its Datenname
    (KAN-486) — the provider did refuse it: the provider answered coherently, and what it
    said was "not this capability". That is the per-capability degradation
    the per-connection breaker would otherwise defeat — one un-entitled
    capability must not take the connection's other capabilities offline.
    (Within `test_connection` alone the breaker could not accumulate anyway,
    since each click's successful access check resets it; the rule matters
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
                actor_id=actor_id,
                purpose=f"entitlement_probe:{probe.capability_code}",
            ) as adapter:
                granted = entitlement_probes.run_probe(probe, adapter)
        except ProviderRejectedError:
            # KAN-488: the login was refused under the probes (the access check
            # had passed). The block charged the breaker once, as a refused
            # login does anywhere; nothing is known about the remaining
            # options, and retrying the login once per option would open the
            # breaker for a connection whose card reads connected — so probing
            # stops here and the remaining rows stay as they were.
            logger.warning(
                "entitlement probing stopped on connection %s: the login was refused at %s; "
                "the remaining options are left unchanged",
                connection.id, probe.capability_code, exc_info=True,
            )
            break
        except Exception:
            logger.warning(
                "entitlement probe for %s failed on connection %s; entitlement left unchanged",
                probe.capability_code, connection.id, exc_info=True,
            )
            continue
        connection_service.record_probed_entitlement(
            db, connection_id=connection.id, capability_code=probe.capability_code, granted=granted
        )
