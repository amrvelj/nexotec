"""KAN-108 — a failing auto-i-dat WSDL fetch is a provider failure like any
other: it counts toward the connection's circuit breaker and writes an
`integration_call_log` row.

`call_capability` used to construct the adapter BEFORE its accounted block.
For `auto_i_dat`, constructing the adapter runs `build_zeep_client`, which
fetches the WSDL over HTTP — so a provider host that hung or refused failed
every call outside the block that charges the breaker and writes the log:
the breaker never opened and the call log showed nothing.

Also pinned here: a transport timeout is logged as `CallStatus.TIMEOUT`
(the value existed and was never written), and a configuration error during
resolution (no `wsdlUrl`) is still charged to nothing.

The real zeep client runs against local sockets only: `_SilentServer`
accepts and never answers, `_refused_url` points at a port nothing listens
on. No provider, no credentials beyond a fake secrets backend.
"""

import functools
import os
import socket

import pytest

from app.integration.adapters import auto_i_dat_soap
from app.integration.errors import ProviderConfigurationError, ProviderTimeoutError, ProviderTransportError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import gateway, resilience
from tests.test_integration_soap_adapter import FakeSecretsBackend, _make_connection, _make_provider
from tests.test_integration_soap_timeout import _WSDL, _run_with_watchdog, _SilentServer

# The gateway writes its log through a session of its own — a second
# database connection, which the SQLite lane cannot give (ADR-011).
pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="needs a second database connection; SQLite shares one across sessions (ADR-011)",
)

_TIMEOUT = 0.3


@pytest.fixture()
def short_transport_timeout(monkeypatch):
    """`_build_real_adapter` calls `build_zeep_client` with the production
    default (10 s); the tests keep the same path and only shorten the wait."""

    monkeypatch.setattr(
        auto_i_dat_soap, "build_zeep_client", functools.partial(auto_i_dat_soap.build_zeep_client, timeout=_TIMEOUT)
    )
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))


def _auto_i_dat_connection(db_session, wsdl_url: str | None):
    connection = _make_connection(db_session, _make_provider(db_session))
    config = dict(connection.config or {})
    if wsdl_url is None:
        config.pop("wsdlUrl", None)
    else:
        config["wsdlUrl"] = wsdl_url
    connection.config = config
    db_session.commit()
    resilience.reset_circuit(connection.id)
    return connection


def _call(db_session, connection) -> None:
    with gateway.call_capability(db_session, connection=connection, capability="system_watermark") as adapter:
        adapter.get_system_watermark()


def _failures(connection) -> int:
    return resilience._circuit_for(connection.id).failure_count


def _logged(db_session, connection) -> list[CallStatus]:
    db_session.expire_all()
    rows = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    return [row.status for row in rows]


def _refused_url() -> str:
    """A port that was free a moment ago and has nothing listening on it."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/Fahrzeuge.asmx?wsdl"


def test_a_wsdl_fetch_that_times_out_is_charged_to_the_breaker_and_logged_as_timeout(
    db_session, short_transport_timeout
):
    with _SilentServer() as server:
        connection = _auto_i_dat_connection(db_session, server.url + "?wsdl")
        exc, _ = _run_with_watchdog(lambda: _call(db_session, connection))

    assert isinstance(exc, ProviderTimeoutError), exc
    assert "WSDL" in str(exc)
    assert _failures(connection) == 1
    assert _logged(db_session, connection) == [CallStatus.TIMEOUT]
    resilience.reset_circuit(connection.id)


def test_a_wsdl_fetch_that_is_refused_is_charged_to_the_breaker_and_logged_as_error(db_session):
    connection = _auto_i_dat_connection(db_session, _refused_url())

    with pytest.raises(ProviderTransportError) as raised:
        _call(db_session, connection)

    assert not isinstance(raised.value, ProviderTimeoutError)
    assert _failures(connection) == 1
    assert _logged(db_session, connection) == [CallStatus.ERROR]
    resilience.reset_circuit(connection.id)


def test_after_the_threshold_the_breaker_opens_and_no_further_call_reaches_the_network(
    db_session, short_transport_timeout
):
    threshold = resilience._FAILURE_THRESHOLD
    with _SilentServer() as server:
        connection = _auto_i_dat_connection(db_session, server.url + "?wsdl")
        for _ in range(threshold):
            exc, _ = _run_with_watchdog(lambda: _call(db_session, connection))
            assert isinstance(exc, ProviderTimeoutError), exc
        reached_before = server.accepted

        with pytest.raises(resilience.CircuitOpenError):
            _call(db_session, connection)
        reached_after = server.accepted

    assert reached_before == threshold  # one WSDL fetch per call: the fetch is not retried
    assert reached_after == reached_before  # the refused call never touched the network
    # The refusal itself is not a provider call: no log row for it.
    assert _logged(db_session, connection) == [CallStatus.TIMEOUT] * threshold
    resilience.reset_circuit(connection.id)


def test_a_missing_wsdl_url_is_neither_charged_to_the_breaker_nor_logged(db_session):
    connection = _auto_i_dat_connection(db_session, None)

    with pytest.raises(ProviderConfigurationError):
        _call(db_session, connection)

    assert _failures(connection) == 0
    assert _logged(db_session, connection) == []


def test_an_operation_that_times_out_inside_the_callers_block_is_logged_as_timeout(
    db_session, monkeypatch, tmp_path, short_transport_timeout
):
    """The WSDL loads from a file; the `Suchen` call goes to a silent server.
    Same status for the same failure, wherever in the call it happens."""

    with _SilentServer() as server:
        wsdl = tmp_path / "fahrzeuge.wsdl"
        wsdl.write_text(_WSDL.format(address=server.url), encoding="utf-8")
        connection = _auto_i_dat_connection(db_session, wsdl.as_uri())
        monkeypatch.setattr(
            auto_i_dat_soap,
            "secrets_backend",
            FakeSecretsBackend({(connection.id, "password"): "s3cret", (connection.id, "aes_key"): "0" * 32}),
        )
        exc, _ = _run_with_watchdog(lambda: _call(db_session, connection))

    assert isinstance(exc, ProviderTimeoutError), exc
    assert _failures(connection) == 1
    assert _logged(db_session, connection) == [CallStatus.TIMEOUT]
    resilience.reset_circuit(connection.id)
