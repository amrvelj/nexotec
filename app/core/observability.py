"""Structured JSON logging with correlationId/tenantId on every line,
OpenTelemetry tracing/metrics, and Sentry error reporting (WP-2 PR-3,
closes G-16).

Wiring is entirely env-driven and every piece degrades to a genuine no-op
when unconfigured, not a silent buffer or a console fallback pretending to
be the real thing:
  - No DMS_OTEL_EXPORTER_OTLP_ENDPOINT -> tracing/metrics stay fully inert.
    The OTel API hands back a no-op tracer/meter when no SDK provider has
    been registered — configure_tracing() below simply never registers one.
  - No DMS_SENTRY_DSN -> configure_sentry() is a no-op.
  - JSON logging with correlationId/tenantId always applies, unconditionally
    — that part needs no external service to be worth having.

No query string leaves the process (KAN-829): a search box's text — a
name, a phone number, an e-mail address, a UID — travels as `?q=`, and
neither the access log, a span nor a Sentry event may carry it (revDSG:
logs sit outside the record's retention and erasure). uvicorn's access line
is scrubbed by `QueryStringAccessLogFilter` (installed by configure_logging,
so whatever command starts the server), spans by `scrub_span_query` (the
FastAPI instrumentation's request hook), Sentry events by
`_drop_query_string` (the query string, and the `Referer`'s query: the SPA
writes a list's search into its own URL). The application's own `app.http`
line never had the query (`httpPath`) and logs no Referer.

correlationId/tenantId are contextvars, not parameters threaded through
every function — the entire point is that a log line logged deep inside a
service function, with no access to the request at all, still carries them.
Both are request-scoped via ObservabilityMiddleware below, which resets
them on the way out so they never leak into a next request handled by the
same worker. The correlation id lives in app.core.correlation (KAN-125):
the outbox, the consumer harness and the daily jobs read and set it too.
"""

import contextvars
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

import jwt
from fastapi import FastAPI
from opentelemetry import metrics, trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import get_settings
from app.core.correlation import correlation_id_from_header, correlation_scope, get_correlation_id
from app.core.redact import REDACTED_PLACEHOLDER, SECRET_FIELDS, is_secret_field

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

CORRELATION_ID_HEADER = "X-Correlation-Id"

# Gauges (WP-2 PR-3): outbox lag, dead-letter depth (the alarms) and consumer
# lag (a dashboard signal, never an alarm — KAN-286). Created
# once against the API-level meter — a genuine no-op (every .set() call
# below a cheap discard) until configure_tracing() registers a real
# MeterProvider, exactly like the tracer. app.worker's heartbeat is what
# actually calls these; see its own module docstring for the poll loop.
_alarm_meter = metrics.get_meter("dms.alarms")
_outbox_lag_gauge = _alarm_meter.create_gauge(
    "dms.outbox.lag_seconds", description="Age of the oldest pending outbox message.", unit="s"
)
_dead_letter_gauge = _alarm_meter.create_gauge(
    "dms.outbox.dead_letter_count", description="Outbox messages currently dead-lettered."
)
_consumer_lag_gauge = _alarm_meter.create_gauge(
    "dms.consumer.lag_seconds",
    description="Time since a consumer last successfully processed an event.",
    unit="s",
)
_reconciliation_age_gauge = _alarm_meter.create_gauge(
    "dms.reconciliation.age_seconds",
    description="Time since the last completed nightly cross-context reconciliation run.",
    unit="s",
)
_label_age_gauge = _alarm_meter.create_gauge(
    "dms.label.age_seconds",
    description="Age of the stalest denormalised cross-context label (CLAUDE.md rule 2), per label.",
    unit="s",
)


def record_outbox_lag_seconds(seconds: float | None) -> None:
    if seconds is not None:
        _outbox_lag_gauge.set(seconds)


def record_dead_letter_count(count: int) -> None:
    _dead_letter_gauge.set(count)


def record_consumer_lag_seconds(consumer_name: str, seconds: float | None) -> None:
    if seconds is not None:
        _consumer_lag_gauge.set(seconds, attributes={"consumer_name": consumer_name})


def record_reconciliation_age_seconds(seconds: float | None) -> None:
    """None means reconciliation has never run — left unset so the alerting
    rule fires on the gauge going stale / absent rather than on a sentinel
    value someone has to remember the meaning of."""

    if seconds is not None:
        _reconciliation_age_gauge.set(seconds)


def record_label_age_seconds(label: str, seconds: float | None) -> None:
    """KAN-84 — sync-age alarm (CLAUDE.md rule 10) for a three-column-pattern
    label kept fresh by a job: `label` names it (e.g. "vehicle_party.vehicle").
    None (never labelled) leaves the gauge unset, like reconciliation age."""

    if seconds is not None:
        _label_age_gauge.set(seconds, attributes={"label": label})

_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)

# What logging.LogRecord already carries before any `extra={}` is merged in
# — subtracted out in JSONLogFormatter so only caller-supplied extra fields
# (app.worker's claimed/published/retried/dead, etc.) end up in the JSON
# body, not internal bookkeeping the caller never asked to log.
_STANDARD_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


def get_tenant_id() -> str | None:
    return _tenant_id.get()


class JSONLogFormatter(logging.Formatter):
    """One JSON object per line, correlationId/tenantId always present
    (null when there is no request in flight — a worker heartbeat, for
    instance) so a log pipeline never has to special-case their absence.

    Redacts any `extra={}` field whose NAME is in app.core.redact's
    SECRET_FIELDS (WP-2 PR-4 — "secrets are never logged, redact at the
    logging boundary") — the same set app.contact.services.contact and
    app.platform.organisation.services.dealership redact with before writing to the audit
    log, applied here too so the same field can't leak back in through a
    log line that happens to carry it.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": get_correlation_id(),
            "tenantId": get_tenant_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS:
                payload[key] = REDACTED_PLACEHOLDER if is_secret_field(key) and value is not None else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _without_query(target: str) -> str:
    return target.split("?", 1)[0]


class QueryStringAccessLogFilter(logging.Filter):
    """KAN-829: uvicorn's access line (`uvicorn.access`, on by default) writes
    the request target with its query string — `"GET /v1/contacts?q=… HTTP/1.1"
    200`. Its record's args are (client, method, target, http version, status);
    the target loses everything from `?` on. The line is kept: it is the
    server's own word that a request arrived."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], _without_query(args[2]), *args[3:])
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # uvicorn configures its own loggers before it imports the app; a filter
    # on the logger itself holds whatever its handlers are (KAN-829).
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QueryStringAccessLogFilter) for f in access.filters):
        access.addFilter(QueryStringAccessLogFilter())


def _otlp_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    headers = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        if key:
            headers[key.strip()] = value.strip()
    return headers


def otel_resource_attributes(process: Literal["web", "worker"]) -> dict[str, str]:
    """KAN-304 — what each process reports as. The worker is its own
    service, `<otel_service_name>-worker` (`dms-platform-worker` by
    default), so Grafana lists it beside the web process rather than mixing
    nightly jobs into the request charts, and an alert on either needs no
    filter to be right. One setting names both: a deployment that renames
    the service renames the worker with it."""

    settings = get_settings()
    service_name = settings.otel_service_name if process == "web" else f"{settings.otel_service_name}-{process}"
    return {"service.name": service_name, "deployment.environment": settings.environment}


# The span attributes that carry a request's URL or its query (old and stable
# HTTP semantic conventions): `http.target` is the path alone already.
_SPAN_URL_ATTRIBUTES = ("http.url", "url.full", "http.target")


def scrub_span_query(span: Any, scope: dict[str, Any]) -> None:
    """KAN-829: the FastAPI (ASGI) instrumentation's server request hook. It
    runs once the server span has its attributes: each URL attribute loses
    its query string, and the stable conventions' `url.query` is emptied (an
    attribute cannot be removed)."""

    attributes = getattr(span, "attributes", None) or {}
    for name in _SPAN_URL_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(name, _without_query(value))
    if attributes.get("url.query"):
        span.set_attribute("url.query", "")


def instrument_app(app: FastAPI, **kwargs: Any) -> None:
    """The one FastAPI instrumentation of the web process (configure_tracing),
    with the query scrubbed from every server span (KAN-829)."""

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, server_request_hook=scrub_span_query, **kwargs)


def configure_tracing(app: FastAPI | None = None, *, process: Literal["web", "worker"] = "web") -> None:
    """Registers the OTLP trace and metric providers. `app` is None for a
    process with no HTTP server (app.worker, KAN-248): it gets the same
    providers — the alarm gauges above are recorded there, and stay no-ops
    until a MeterProvider exists in that process — and no FastAPI
    instrumentation. `process` names the service it reports as
    (`otel_resource_attributes`)."""

    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint:
        return

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(otel_resource_attributes(process))
    headers = _otlp_headers(settings.otel_exporter_otlp_headers)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces", headers=headers))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/metrics", headers=headers)
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    # RED metrics per endpoint (request count, duration, error rate) are
    # this instrumentor's own job — it emits http.server.duration and
    # friends automatically once a MeterProvider is registered above; nothing
    # here hand-rolls that. Distributed traces across the HTTP boundary are
    # the same call.
    if app is not None:
        instrument_app(app)


def _sentry_secret_names() -> list[str]:
    """SECRET_FIELDS as Sentry's scrubber sees them: lower-cased, snake_case
    (`secret_value`) and camelCase (`secretValue` -> `secretvalue`)."""

    return sorted(SECRET_FIELDS | {name.replace("_", "") for name in SECRET_FIELDS})


def _drop_query_string(event: "Event", hint: "Hint") -> "Event | None":
    """KAN-829: Sentry's ASGI integration sends the request's query string
    (`request.query_string`) and its headers with every error and
    transaction. The query string never leaves, and neither does the
    `Referer`'s: the SPA is served from the same origin and writes a list's
    search into its own URL (`/contacts?q=…`), so every API call from that
    page names the search in its Referer. `request.url` is already without
    a query; it is cut too, should that change."""

    request = event.get("request")
    if isinstance(request, dict):
        request.pop("query_string", None)
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = _without_query(url)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name, value in list(headers.items()):
                if isinstance(name, str) and name.lower() == "referer" and isinstance(value, str):
                    headers[name] = _without_query(value)
    return event


def configure_sentry() -> None:
    """Secrets are never logged, and a Sentry event is a log line that leaves
    the process: no stack-frame locals (an adapter holds a resolved secret in
    one — KAN-248, now that the worker reports too), and app.core.redact's
    SECRET_FIELDS scrubbed by name, recursively, on top of Sentry's own
    denylist (the same boundary JSONLogFormatter applies). Sentry compares
    lower-cased names exactly, and request bodies are camelCase on the wire
    (`secretValue`, `taxId`), so each name is listed in both spellings."""

    settings = get_settings()
    if not settings.sentry_dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        include_local_variables=False,
        event_scrubber=EventScrubber(denylist=[*DEFAULT_DENYLIST, *_sentry_secret_names()], recursive=True),
        integrations=[FastApiIntegration(), LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        before_send=_drop_query_string,
        before_send_transaction=_drop_query_string,
    )


def _peek_tenant_id(request: Request) -> str | None:
    """Best-effort, UNVERIFIED decode purely to tag a log line — this is
    not an authorization decision and must never be treated as one. The
    real, verified decode is app.core.auth.get_current_principal, reached
    independently through each route's own dependency; a forged token here
    only pollutes the forger's own log line, since nothing downstream
    trusts this value for access control.
    """

    token = None
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    else:
        token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    tenant_id = claims.get("tenant_id")
    return tenant_id if isinstance(tenant_id, str) else None


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates correlationId, tags tenantId, and logs one
    structured line per request (method, path, status, durationMs) — the
    line every request produces regardless of what else it logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._logger = logging.getLogger("app.http")

    async def dispatch(self, request: Request, call_next) -> Response:
        # KAN-125: the id every event this request publishes carries (app.core.outbox.publish).
        # A header that is not a UUID is caller input the outbox cannot store: a fresh id
        # replaces it, and the response header says which.
        with correlation_scope(correlation_id_from_header(request.headers.get(CORRELATION_ID_HEADER))) as scoped:
            return await self._dispatch(request, call_next, str(scoped))

    async def _dispatch(self, request: Request, call_next, correlation_id: str) -> Response:
        tenant_id = _peek_tenant_id(request)
        tenant_token = _tenant_id.set(tenant_id)

        # Tags whatever span is active for this request — FastAPIInstrumentor's
        # own auto-created one when tracing is configured, the OTel API's
        # no-op span otherwise (set_attribute on it is a harmless no-op).
        # This is what makes "the same correlationId is findable in both the
        # trace and the log line" literally true rather than merely co-located.
        span = trace.get_current_span()
        span.set_attribute("correlation_id", correlation_id)
        if tenant_id:
            span.set_attribute("tenant_id", tenant_id)

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            self._logger.exception(
                "http_request",
                extra={"httpMethod": request.method, "httpPath": request.url.path, "httpStatus": 500},
            )
            raise
        else:
            duration_ms = (time.monotonic() - start) * 1000
            self._logger.info(
                "http_request",
                extra={
                    "httpMethod": request.method,
                    "httpPath": request.url.path,
                    "httpStatus": response.status_code,
                    "durationMs": round(duration_ms, 2),
                },
            )
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            _tenant_id.reset(tenant_token)
