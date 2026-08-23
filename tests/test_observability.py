"""WP-2 PR-3 (closes G-16): correlationId propagation, structured JSON
logs, and /readyz's real dependency check.
"""

import json
import logging
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.observability import (
    CORRELATION_ID_HEADER,
    JSONLogFormatter,
    ObservabilityMiddleware,
    get_correlation_id,
    get_tenant_id,
)
from app.db import get_db

# --- correlationId propagation --------------------------------------------------------


@pytest.fixture()
def observed_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/probe")
    def probe():
        return {"correlationId": get_correlation_id(), "tenantId": get_tenant_id()}

    return TestClient(app)


def test_a_correlation_id_is_generated_when_none_is_supplied(observed_client):
    response = observed_client.get("/probe")
    assert response.status_code == 200
    header_value = response.headers[CORRELATION_ID_HEADER]
    assert header_value
    # It's what the handler itself saw via the contextvar, not something
    # bolted on after the fact.
    assert response.json()["correlationId"] == header_value


def test_a_supplied_correlation_id_is_echoed_back_unchanged(observed_client):
    supplied = str(uuid.uuid4())
    response = observed_client.get("/probe", headers={CORRELATION_ID_HEADER: supplied})
    assert response.headers[CORRELATION_ID_HEADER] == supplied
    assert response.json()["correlationId"] == supplied


def test_two_requests_get_different_correlation_ids(observed_client):
    first = observed_client.get("/probe").headers[CORRELATION_ID_HEADER]
    second = observed_client.get("/probe").headers[CORRELATION_ID_HEADER]
    assert first != second


def test_tenant_id_is_tagged_from_an_unverified_bearer_token(observed_client):
    from app.core.auth import AccessRole, create_access_token

    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, roles=frozenset({AccessRole.SALES})
    )
    response = observed_client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["tenantId"] == str(tenant_id)


def test_tenant_id_is_none_for_an_unauthenticated_request(observed_client):
    response = observed_client.get("/probe")
    assert response.json()["tenantId"] is None


# --- JSON log formatting ---------------------------------------------------------------


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JSONLogFormatter().format(record))


def test_json_formatter_produces_valid_json_with_the_core_fields():
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=(), exc_info=None
    )
    payload = _format(record)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload
    assert "correlationId" in payload  # present (possibly null), never silently dropped
    assert "tenantId" in payload


def test_json_formatter_includes_caller_supplied_extra_fields():
    record = logging.LogRecord(
        name="app.worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="outbox poll",
        args=(),
        exc_info=None,
    )
    record.claimed = 3
    record.published = 2
    payload = _format(record)
    assert payload["claimed"] == 3
    assert payload["published"] == 2


def test_json_formatter_does_not_leak_internal_logrecord_bookkeeping():
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=(), exc_info=None
    )
    payload = _format(record)
    # Internal LogRecord attributes an operator never asked to log.
    assert "pathname" not in payload
    assert "args" not in payload
    assert "msg" not in payload


# --- /readyz --------------------------------------------------------------------------


def test_readyz_is_200_when_the_database_is_reachable(client):
    response = client.get("/v1/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_is_503_when_the_database_is_unreachable():
    app = FastAPI()
    from app.platform.api.health import router

    app.include_router(router, prefix="/v1")

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    def _broken_get_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_get_db
    response = TestClient(app).get("/v1/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_healthz_does_not_touch_the_database(client, monkeypatch):
    """Liveness must stay a static reply — see health.py's own docstring
    on why a DB-touching liveness probe is the wrong design, not an
    oversight. Verified here by making any DB access raise and confirming
    /healthz is unaffected.
    """

    def _boom(*args, **kwargs):
        raise AssertionError("healthz must never touch the database")

    monkeypatch.setattr(Session, "execute", _boom)
    response = client.get("/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- the actual exit criterion, end to end ---------------------------------------------


class _CapturingHandler(logging.Handler):
    """Formats at emit() time, unlike caplog's raw-record capture — the
    correlationId contextvar this test cares about is reset by the
    middleware's `finally` block the moment the request finishes, so
    formatting has to happen while it's still in scope, not afterward.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JSONLogFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def test_a_requests_correlation_id_is_findable_in_both_the_trace_and_the_log_line():
    """WP-2 PR-3's exit criterion, literally: registers a real TracerProvider
    with an in-memory exporter (no live collector needed to prove the
    mechanism), sends one request, and asserts the SAME correlationId
    value appears as a span attribute AND in the structured log line —
    not just that both happen to exist.
    """

    from opentelemetry import trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous_provider = trace_api.get_tracer_provider()
    trace_api.set_tracer_provider(provider)
    tracer = trace_api.get_tracer("test")

    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    http_logger = logging.getLogger("app.http")
    capture = _CapturingHandler()
    http_logger.addHandler(capture)
    try:
        with tracer.start_as_current_span("http_request"):
            response = TestClient(app).get("/probe")
    finally:
        http_logger.removeHandler(capture)
        trace_api.set_tracer_provider(previous_provider)

    correlation_id = response.headers[CORRELATION_ID_HEADER]

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["correlation_id"] == correlation_id

    assert len(capture.lines) == 1
    logged_payload = json.loads(capture.lines[0])
    assert logged_payload["correlationId"] == correlation_id


# --- redaction at the logging boundary (WP-2 PR-4) --------------------------------------


def test_json_formatter_redacts_a_secret_named_extra_field():
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=(), exc_info=None
    )
    record.tax_id = "CHE-123.456.789"
    record.first_name = "Anna"
    payload = _format(record)
    assert payload["tax_id"] == "***redacted***"
    assert payload["first_name"] == "Anna"
