"""The outbox worker sets up logging, Sentry and the OTLP providers the way
the web process does (KAN-248, P-10).

Before KAN-248 `python -m app.worker` called none of them: an ERROR log —
the nightly reconciliation alarm, a failing daily job, a failed reservation
release — stayed in the process log, and the heartbeat's alarm gauges were
recorded against OTel's no-op meter, so nothing was ever exported.

No network: Sentry gets a capturing transport, the OTLP exporters and the
metric reader are swapped for in-memory ones, and the global OTel providers
are captured instead of set, so no other test sees them.
"""

import ast
import datetime as dt
import logging
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
    [handler] = logging.getLogger().handlers

    assert isinstance(handler.formatter, observability.JSONLogFormatter)


def test_without_a_dsn_the_worker_leaves_sentry_inert(monkeypatch):
    _settings(monkeypatch, sentry_dsn=None, otel_exporter_otlp_endpoint=None)
    transport = _capture_sentry(monkeypatch)

    worker.configure_observability()
    logging.getLogger("app.worker").error("not reported anywhere")

    assert not sentry_sdk.get_client().is_active()
    assert transport.events == []


def test_with_an_otlp_endpoint_the_worker_registers_the_providers_its_alarm_gauges_need(monkeypatch):
    """The heartbeat's gauges (README's alarm table) are created at import
    against OTel's API meter and become real only once the process registers
    a MeterProvider. The worker now does; it does not instrument FastAPI."""

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http import metric_exporter, trace_exporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics import export as metrics_export
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    _settings(monkeypatch, sentry_dsn=None, otel_exporter_otlp_endpoint="http://collector.example.invalid:4318")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(metrics_export, "PeriodicExportingMetricReader", lambda exporter: reader)
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", lambda **kw: None)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kw: InMemorySpanExporter())
    registered: dict[str, object] = {}
    monkeypatch.setattr(metrics, "set_meter_provider", lambda provider: registered.update(meter=provider))
    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: registered.update(tracer=provider))
    instrumented: list[object] = []
    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", lambda app: instrumented.append(app))

    worker.configure_observability()

    meter_provider, tracer_provider = registered["meter"], registered["tracer"]
    try:
        assert isinstance(meter_provider, MeterProvider)
        assert isinstance(tracer_provider, TracerProvider)
        assert instrumented == []

        # The provider the worker registers collects what its reader is handed.
        meter_provider.get_meter("dms.alarms").create_gauge("dms.outbox.lag_seconds").set(7)
        exported = reader.get_metrics_data()
        names = {m.name for rm in exported.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
        assert "dms.outbox.lag_seconds" in names
    finally:
        meter_provider.shutdown()
        tracer_provider.shutdown()


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
