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

correlationId/tenantId are contextvars, not parameters threaded through
every function — the entire point is that a log line logged deep inside a
service function, with no access to the request at all, still carries them.
Both are request-scoped via ObservabilityMiddleware below, which resets
them on the way out so they never leak into a next request handled by the
same worker.
"""

import contextvars
import json
import logging
import time
import uuid
from typing import Any

import jwt
from fastapi import FastAPI
from opentelemetry import metrics, trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import get_settings
from app.core.redact import REDACTED_PLACEHOLDER, is_secret_field

CORRELATION_ID_HEADER = "X-Correlation-Id"

# Alarms (WP-2 PR-3): outbox lag, dead-letter depth, consumer lag. Created
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


def record_outbox_lag_seconds(seconds: float | None) -> None:
    if seconds is not None:
        _outbox_lag_gauge.set(seconds)


def record_dead_letter_count(count: int) -> None:
    _dead_letter_gauge.set(count)


def record_consumer_lag_seconds(consumer_name: str, seconds: float | None) -> None:
    if seconds is not None:
        _consumer_lag_gauge.set(seconds, attributes={"consumer_name": consumer_name})

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
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


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def get_tenant_id() -> str | None:
    return _tenant_id.get()


class JSONLogFormatter(logging.Formatter):
    """One JSON object per line, correlationId/tenantId always present
    (null when there is no request in flight — a worker heartbeat, for
    instance) so a log pipeline never has to special-case their absence.

    Redacts any `extra={}` field whose NAME is in app.core.redact's
    SECRET_FIELDS (WP-2 PR-4 — "secrets are never logged, redact at the
    logging boundary") — the same set app.customer.services.customer and
    app.platform.services.dealer redact with before writing to the audit
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


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _otlp_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    headers = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        if key:
            headers[key.strip()] = value.strip()
    return headers


def configure_tracing(app: FastAPI) -> None:
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint:
        return

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": settings.otel_service_name, "deployment.environment": settings.environment})
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
    FastAPIInstrumentor.instrument_app(app)


def configure_sentry() -> None:
    settings = get_settings()
    if not settings.sentry_dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        integrations=[FastApiIntegration(), LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
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
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        correlation_token = _correlation_id.set(correlation_id)
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
            _correlation_id.reset(correlation_token)
            _tenant_id.reset(tenant_token)
