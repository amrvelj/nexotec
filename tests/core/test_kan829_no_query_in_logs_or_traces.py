"""KAN-829: a search box's text travels as `?q=` (`/v1/contacts`, the
duplicate check, other lists) — a name, a phone number, an e-mail address, a
UID. It reaches no log line, no span attribute and no Sentry event (revDSG:
logs sit outside the record's retention and erasure).

The access log is proven on the server as production starts it (Dockerfile's
CMD, render.yaml's startCommand: plain `uvicorn app.main:app`, access log on);
the span on an app instrumented by the very function `configure_tracing`
uses; the Sentry event through the very `configure_sentry`."""

import json
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import observability
from tests.core.test_kan499_deployment_environment import _startup_env
from tests.core.test_worker_observability import (  # noqa: F401 — the autouse fixture puts Sentry and logging back
    _FAKE_DSN,
    _capture_sentry,
    _isolated_process_state,
    _settings,
)

_REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_production_starts_plain_uvicorn_with_its_access_log_on():
    """The premise of the next test: production's command adds no flag that
    would change the access log, so the app itself must scrub it."""

    dockerfile = (_REPO / "Dockerfile").read_text()
    assert re.search(r"uvicorn app\.main:app --host 0\.0\.0\.0 --port 8000\"", dockerfile)
    blueprint = yaml.safe_load((_REPO / "render.yaml").read_text())
    commands = [s.get("startCommand", "") for s in blueprint["services"] if "uvicorn" in s.get("startCommand", "")]
    assert commands and all("app.main:app" in c and "--no-access-log" not in c for c in commands)


def test_the_access_log_of_the_server_as_production_starts_it_carries_no_search_text(tmp_path):
    marker = f"CHE-{uuid.uuid4().int % 10**9:09d}"
    port = _free_port()
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=_REPO, env=_startup_env(DMS_ENVIRONMENT="development"), stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    httpx.get(f"http://127.0.0.1:{port}/v1/contacts?q={marker}", timeout=5)
                    break
                except httpx.TransportError:
                    assert server.poll() is None, log_path.read_text()
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(0.3)
            httpx.get(f"http://127.0.0.1:{port}/v1/contacts/duplicate-check?q=anna%40garage.ch&{marker}=1", timeout=5)
            time.sleep(0.5)
        finally:
            server.terminate()
            server.wait(timeout=30)
    output = log_path.read_text()
    # The access line is still written — only its query is gone.
    assert '"GET /v1/contacts HTTP/1.1" 401' in output, output
    assert '"GET /v1/contacts/duplicate-check HTTP/1.1"' in output, output
    assert marker not in output
    assert "garage.ch" not in output


def test_the_server_span_carries_no_search_text():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()
    app.add_middleware(observability.ObservabilityMiddleware)

    @app.get("/v1/contacts")
    def contacts(q: str = "") -> dict:
        return {"items": []}

    observability.instrument_app(app, tracer_provider=provider)
    marker = f"Mueller-{uuid.uuid4().hex[:8]}"
    assert TestClient(app).get(f"/v1/contacts?q={marker}&sort=name").status_code == 200

    spans = exporter.get_finished_spans()
    [server] = [span for span in spans if span.attributes.get("http.route") == "/v1/contacts" and span.parent is None]
    assert server.attributes["http.url"] == "http://testserver/v1/contacts"
    assert server.attributes["http.target"] == "/v1/contacts"
    for span in spans:
        assert marker not in json.dumps(dict(span.attributes), default=str)
        assert marker not in span.name


def test_a_sentry_event_carries_no_search_text(monkeypatch):
    _settings(monkeypatch, sentry_dsn=_FAKE_DSN, otel_exporter_otlp_endpoint=None)
    transport = _capture_sentry(monkeypatch)
    observability.configure_sentry()
    app = FastAPI()

    @app.get("/v1/contacts")
    def contacts(q: str = "") -> dict:
        raise RuntimeError("kan-829 probe")

    marker = f"anna-{uuid.uuid4().hex[:8]}"
    response = TestClient(app, raise_server_exceptions=False).get(f"/v1/contacts?q={marker}")
    assert response.status_code == 500
    [event] = [e for e in transport.events if e.get("request")]
    assert event["request"]["url"].endswith("/v1/contacts")
    assert "query_string" not in event["request"]
    assert marker not in json.dumps(event, default=str)


def test_the_filter_leaves_a_record_without_a_query_as_it_is():
    import logging

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", "/v1/health", "1.1", 200), None)
    assert observability.QueryStringAccessLogFilter().filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /v1/health HTTP/1.1" 200'
