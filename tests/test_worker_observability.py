"""The outbox worker sets up logging, Sentry and the OTLP providers the way
the web process does (KAN-248, P-10).

Before KAN-248 `python -m app.worker` called none of them: an ERROR log —
the nightly reconciliation alarm, a failing daily job, a failed reservation
release — stayed in the process log, and the heartbeat's alarm gauges were
recorded against OTel's no-op meter, so nothing was ever exported.

No network: Sentry gets a capturing transport, and the OTLP exporters and
the metric reader are swapped for in-memory ones. The metrics test runs the
worker's set-up in a subprocess: OTel's global providers can be set once per
process, and no other test should run under them.
"""

import ast
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app import worker
from app.core import observability
from app.core.config import get_settings
from app.core.daily_scheduler import _REGISTRY, register_daily_job, run_due_daily_jobs

_FAKE_DSN = "https://public@sentry.example.invalid/1"


class _CapturingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture(autouse=True)
def _isolated_process_state():
    """configure_logging replaces the root handlers and configure_sentry binds
    a global client; the scheduler registry is module-global. Put all three
    back so no other test runs under this one's set-up."""

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    root.handlers, root.level = handlers, level


def _settings(monkeypatch, **overrides):
    settings = get_settings().model_copy(update=overrides)
    monkeypatch.setattr(observability, "get_settings", lambda: settings)


def _capture_sentry(monkeypatch) -> _CapturingTransport:
    transport = _CapturingTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **kw: real_init(*a, transport=transport, **kw))
    return transport


def test_an_error_logged_by_a_failing_daily_job_in_the_worker_becomes_a_sentry_event(db_session, monkeypatch):
    _settings(monkeypatch, sentry_dsn=_FAKE_DSN, otel_exporter_otlp_endpoint=None)
    transport = _capture_sentry(monkeypatch)

    worker.configure_observability()

    def _failing_job(db):
        raise RuntimeError("kan-248 induced fault")

    register_daily_job("kan248.failing", _failing_job)
    ran = run_due_daily_jobs(db_session, today=dt.date(2026, 10, 11))

    assert ran == []
    [event] = transport.events
    assert event["level"] == "error"
    assert event["logger"] == "app.core.daily_scheduler"
    [exception] = event["exception"]["values"]
    assert exception["type"] == "RuntimeError"
    assert exception["value"] == "kan-248 induced fault"


def test_the_worker_writes_json_log_lines_with_extra_fields(monkeypatch, capsys):
    """configure_logging, as app/main.py runs it: the worker's `extra` fields
    (the poll counts, the heartbeat's gauges) reach the log line."""

    _settings(monkeypatch, sentry_dsn=None, otel_exporter_otlp_endpoint=None)

    worker.configure_observability()
    logging.getLogger("app.worker").info("outbox poll", extra={"claimed": 3, "dead": 0})

    [line] = [json.loads(raw) for raw in capsys.readouterr().err.splitlines() if '"outbox poll"' in raw]
    assert line["logger"] == "app.worker"
    assert (line["claimed"], line["dead"]) == (3, 0)


def test_a_sentry_event_carries_no_secret_from_a_frame_or_an_extra_field(db_session, monkeypatch):
    """Secrets are never logged (CLAUDE.md): no stack-frame locals, and
    app.core.redact's SECRET_FIELDS scrubbed by name."""

    _settings(monkeypatch, sentry_dsn=_FAKE_DSN, otel_exporter_otlp_endpoint=None)
    transport = _capture_sentry(monkeypatch)
    worker.configure_observability()

    # Made at run time: the event carries source-context lines, so a literal
    # would be found there whether or not a value leaked.
    local_secret, extra_secret = f"local-{uuid.uuid4().hex}", f"extra-{uuid.uuid4().hex}"

    def _adapter_call(value):
        client_secret = value  # noqa: F841 - the frame local under test
        raise RuntimeError("provider refused")

    try:
        _adapter_call(local_secret)
    except RuntimeError:
        logging.getLogger("app.integration").exception(
            "provider call failed", extra={"client_secret": extra_secret, "provider": "auto_i_dat"}
        )

    [event] = transport.events
    serialized = json.dumps(event, default=str)
    assert local_secret not in serialized
    assert extra_secret not in serialized
    assert "auto_i_dat" in serialized  # an ordinary extra field still arrives


def test_without_a_dsn_the_worker_leaves_sentry_inert(monkeypatch):
    _settings(monkeypatch, sentry_dsn=None, otel_exporter_otlp_endpoint=None)
    transport = _capture_sentry(monkeypatch)

    worker.configure_observability()
    logging.getLogger("app.worker").error("not reported anywhere")

    assert not sentry_sdk.get_client().is_active()
    assert transport.events == []


_GAUGE_PROBE = """
import json
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http import trace_exporter
from opentelemetry.sdk.metrics import export as metrics_export
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

reader = InMemoryMetricReader()
metrics_export.PeriodicExportingMetricReader = lambda exporter: reader
trace_exporter.OTLPSpanExporter = lambda **kw: InMemorySpanExporter()

from app import worker
from app.core import observability

worker.configure_observability()
observability.record_outbox_lag_seconds(7)
observability.record_dead_letter_count(0)
observability.record_consumer_lag_seconds("inventory.sales_contract_confirmed", 3)
observability.record_reconciliation_age_seconds(60)
observability.record_label_age_seconds("vehicle_party.vehicle", 120)
data = reader.get_metrics_data()
print(json.dumps({
    "metrics": sorted({m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}),
    "tracerProvider": type(trace.get_tracer_provider()).__name__,
}))
"""


def test_with_an_otlp_endpoint_the_heartbeat_alarm_gauges_are_exported_from_the_worker():
    """The heartbeat's gauges (README's alarm table) are created at import
    against OTel's API meter and become real only once the process registers
    a MeterProvider. Before KAN-248 the worker registered none, so this list
    was empty. A fresh process, so the global providers stay out of every
    other test."""

    env = {**os.environ, "DMS_OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example.invalid:4318"}
    env.pop("DMS_SENTRY_DSN", None)
    result = subprocess.run(
        [sys.executable, "-c", _GAUGE_PROBE],
        cwd=Path(worker.__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    probe = json.loads(result.stdout.strip().splitlines()[-1])

    assert probe["metrics"] == [
        "dms.consumer.lag_seconds",
        "dms.label.age_seconds",
        "dms.outbox.dead_letter_count",
        "dms.outbox.lag_seconds",
        "dms.reconciliation.age_seconds",
    ]
    assert probe["tracerProvider"] == "TracerProvider"


def test_the_worker_entrypoint_configures_observability_before_it_runs():
    """`python -m app.worker` is the only caller; pinned so the call cannot be
    dropped from the `__main__` block without failing the build."""

    tree = ast.parse(Path(worker.__file__).read_text())
    [main_block] = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and ast.unparse(node.test) == "__name__ == '__main__'"
    ]
    calls = [ast.unparse(stmt.value.func) for stmt in main_block.body if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)]

    assert calls[0] == "configure_observability"
    assert "run" in calls
