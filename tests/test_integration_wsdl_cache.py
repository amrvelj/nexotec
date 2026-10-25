"""KAN-213 — the auto-i-dat WSDL is loaded once per connection, not once per
call.

Every `call_capability` resolves a fresh adapter, and the real factory built a
fresh `zeep.Client(wsdl_url)` — which fetches and parses the WSDL in its
constructor. Since KAN-78 the catalogue sync resolves one adapter per FzKey, so
a full seed of N variants loaded the same WSDL N+2 times.

The real zeep client runs against `_ProviderServer`, a local HTTP server that
serves the hand-written WSDL on GET (counting each load) and answers every
SOAP POST with an encrypted `System` response, so each call goes the whole
production path: gateway → `_build_real_adapter` → zeep → `Suchen` → decrypt.
"""

import datetime as dt
import http.server
import os
import threading
from typing import Self

import pytest

from app.core.audit import list_audit_events
from app.integration.adapters import auto_i_dat_soap
from app.integration.errors import ProviderTransportError
from app.integration.models.provider import IntegrationProvider
from app.integration.services import gateway, resilience
from tests.test_integration_soap_adapter import (
    _AES_KEY,
    _SPEC_XML,
    FakeSecretsBackend,
    _encrypt_for_wire,
    _make_connection,
    _make_provider,
)
from tests.test_integration_soap_timeout import _WSDL

# The gateway writes its log through a session of its own — a second
# database connection, which the SQLite lane cannot give (ADR-011).
pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="needs a second database connection; SQLite shares one across sessions (ADR-011)",
)

_SOAP_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <SuchenResponse xmlns="http://test.invalid/fahrzeuge"><SuchenResult>{result}</SuchenResult></SuchenResponse>
  </soap:Body>
</soap:Envelope>"""


class _ProviderServer:
    """Serves the WSDL (pointing back at itself) and answers `Suchen`.
    `failing_loads` makes the first N WSDL loads a 500."""

    def __init__(self, *, failing_loads: int = 0) -> None:
        self.wsdl_loads = 0
        self.soap_calls = 0
        self._failing_loads = failing_loads
        server = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                server.wsdl_loads += 1
                if server.wsdl_loads <= server._failing_loads:
                    self._send(500, b"")
                    return
                self._send(200, _WSDL.format(address=server.endpoint).encode("utf-8"))

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                server.soap_calls += 1
                result = _encrypt_for_wire(_SPEC_XML["System"])
                self._send(200, _SOAP_RESPONSE.format(result=result).encode("utf-8"))

            def log_message(self, *args: object) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.endpoint = f"http://127.0.0.1:{self._server.server_address[1]}/Fahrzeuge.asmx"
        self.url = self.endpoint + "?wsdl"

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(autouse=True)
def _empty_wsdl_cache():
    auto_i_dat_soap.clear_wsdl_cache()
    yield
    auto_i_dat_soap.clear_wsdl_cache()


@pytest.fixture()
def secrets(monkeypatch) -> dict:
    values: dict = {}
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", FakeSecretsBackend(values))
    return values


def _connection(db_session, secrets: dict, wsdl_url: str, *, provider=None):
    connection = _make_connection(db_session, provider or _make_provider(db_session))
    connection.config = {**(connection.config or {}), "wsdlUrl": wsdl_url, "username": "dealer"}
    db_session.commit()
    resilience.reset_circuit(connection.id)
    secrets[(connection.id, "password")] = "s3cret"
    secrets[(connection.id, "aes_key")] = _AES_KEY.decode("ascii")
    return connection


def _call(db_session, connection) -> None:
    with gateway.call_capability(db_session, connection=connection, capability="system_watermark") as adapter:
        assert adapter.get_system_watermark().update_date is not None


def test_n_calls_on_one_connection_load_the_wsdl_once(db_session, secrets):
    with _ProviderServer() as server:
        connection = _connection(db_session, secrets, server.url)
        for _ in range(5):
            _call(db_session, connection)

    assert server.wsdl_loads == 1
    assert server.soap_calls == 5


def test_each_call_still_resolves_and_audits_its_credentials(db_session, secrets):
    """Exit criterion 2: the cached WSDL holds no secret, so the password and
    the AES key are resolved — and audit-logged — on every call, as before."""

    with _ProviderServer() as server:
        connection = _connection(db_session, secrets, server.url)
        for _ in range(3):
            _call(db_session, connection)

    resolved = [
        event.reason
        for event in list_audit_events(
            db_session, entity_type="integration_secret_ref", entity_id=connection.id, tenant_id=connection.tenant_id
        )
        if event.action == "secret_resolved"
    ]
    assert sorted(resolved) == sorted(
        ["slot=password purpose=system_watermark", "slot=aes_key purpose=system_watermark"] * 3
    )


def test_every_call_gets_its_own_client_and_transport(db_session, secrets):
    """Only the parsed document is shared: no HTTP session crosses threads,
    and each transport keeps the KAN-76 timeouts."""

    with _ProviderServer() as server:
        connection = _connection(db_session, secrets, server.url)
        first = auto_i_dat_soap._build_real_adapter(db_session, connection, None, "vehicle_data")
        second = auto_i_dat_soap._build_real_adapter(db_session, connection, None, "vehicle_data")

    first_client, second_client = first._client._client, second._client._client
    assert first_client is not second_client
    assert first_client.transport is not second_client.transport
    assert first_client.wsdl is second_client.wsdl
    assert second_client.transport.load_timeout == resilience.DEFAULT_TIMEOUT_SECONDS
    assert second_client.transport.operation_timeout == resilience.DEFAULT_TIMEOUT_SECONDS
    assert server.wsdl_loads == 1


def test_a_changed_wsdl_url_loads_the_new_wsdl(db_session, secrets):
    with _ProviderServer() as old, _ProviderServer() as new:
        connection = _connection(db_session, secrets, old.url)
        _call(db_session, connection)
        connection.config = {**connection.config, "wsdlUrl": new.url}
        db_session.commit()
        _call(db_session, connection)
        _call(db_session, connection)

    assert (old.wsdl_loads, new.wsdl_loads) == (1, 1)
    assert (old.soap_calls, new.soap_calls) == (1, 2)


def test_a_rotated_credential_reloads_the_wsdl(db_session, secrets):
    with _ProviderServer() as server:
        connection = _connection(db_session, secrets, server.url)
        _call(db_session, connection)
        # What `connections.set_secret` stamps on every create or rotation.
        connection.rotated_at = dt.datetime.now(dt.UTC)
        db_session.commit()
        _call(db_session, connection)

    assert server.wsdl_loads == 2


def test_a_failed_load_is_not_cached_and_the_next_call_retries(db_session, secrets):
    with _ProviderServer(failing_loads=1) as server:
        connection = _connection(db_session, secrets, server.url)
        with pytest.raises(ProviderTransportError, match="WSDL"):
            _call(db_session, connection)
        # Exit criterion 3: the failed build is still raised by the gateway's
        # adapter resolution and charged to the breaker (KAN-108).
        assert resilience._circuit_for(connection.id).failure_count == 1
        _call(db_session, connection)
        _call(db_session, connection)

    assert server.wsdl_loads == 2
    assert server.soap_calls == 2


def test_an_expired_entry_is_loaded_again(db_session, secrets, monkeypatch):
    monkeypatch.setattr(auto_i_dat_soap, "_WSDL_CACHE_SECONDS", 0.0)
    with _ProviderServer() as server:
        connection = _connection(db_session, secrets, server.url)
        _call(db_session, connection)
        _call(db_session, connection)

    assert server.wsdl_loads == 2


def _provider_of(db_session, connection):
    return db_session.get(IntegrationProvider, connection.provider_id)


def test_two_connections_never_share_a_wsdl(db_session, secrets):
    with _ProviderServer() as server:
        first = _connection(db_session, secrets, server.url)
        _call(db_session, first)
        second = _connection(db_session, secrets, server.url, provider=_provider_of(db_session, first))
        _call(db_session, second)
        _call(db_session, first)

    assert server.wsdl_loads == 2
