"""KAN-76 (Gap G-47) — the real SOAP transport is bounded by a timeout.

``build_zeep_client`` used to construct ``zeep.Client(wsdl_url)`` with zeep's
default ``Transport``: 300 s to load the WSDL and **no timeout at all** on an
operation call (``Transport(timeout=300, operation_timeout=None)`` in zeep
4.3). A provider that accepts the TCP connection and never answers held the
request thread forever — twice, once per ``call_with_retry`` attempt.

These tests run the real zeep client against a local socket that accepts
connections and never writes a byte. No real network, no WSDL from the
provider, no credentials: the WSDL below is hand-written with the one
``Suchen`` operation of the real service's shape, and loaded from a file.
Every blocking call runs in a worker thread with its own deadline, so a
regression fails the test instead of hanging the test process.
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest

from app.integration.adapters import auto_i_dat_soap
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter, build_zeep_client
from app.integration.errors import ProviderTransportError
from app.integration.services import resilience
from tests.test_integration_soap_adapter import FakeSecretsBackend, _make_connection, _make_provider

_TIMEOUT = 0.5
# Far longer than any bounded call below can take, far shorter than "hung".
_WATCHDOG_SECONDS = 15.0

_WSDL = """<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:tns="http://test.invalid/fahrzeuge"
    targetNamespace="http://test.invalid/fahrzeuge">
  <wsdl:types>
    <xs:schema elementFormDefault="qualified" targetNamespace="http://test.invalid/fahrzeuge">
      <xs:element name="Suchen">
        <xs:complexType><xs:sequence>
          <xs:element name="Benutzername" type="xs:string"/>
          <xs:element name="Passwort" type="xs:string"/>
          <xs:element name="Sprache" type="xs:string"/>
          <xs:element name="Datenname" type="xs:string"/>
          <xs:element name="Suchwerte" type="xs:string"/>
          <xs:element name="Einstellungen" type="xs:string"/>
        </xs:sequence></xs:complexType>
      </xs:element>
      <xs:element name="SuchenResponse">
        <xs:complexType><xs:sequence>
          <xs:element name="SuchenResult" type="xs:string"/>
        </xs:sequence></xs:complexType>
      </xs:element>
    </xs:schema>
  </wsdl:types>
  <wsdl:message name="SuchenIn"><wsdl:part name="parameters" element="tns:Suchen"/></wsdl:message>
  <wsdl:message name="SuchenOut"><wsdl:part name="parameters" element="tns:SuchenResponse"/></wsdl:message>
  <wsdl:portType name="FahrzeugeSoap">
    <wsdl:operation name="Suchen">
      <wsdl:input message="tns:SuchenIn"/>
      <wsdl:output message="tns:SuchenOut"/>
    </wsdl:operation>
  </wsdl:portType>
  <wsdl:binding name="FahrzeugeSoap" type="tns:FahrzeugeSoap">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="Suchen">
      <soap:operation soapAction="http://test.invalid/fahrzeuge/Suchen" style="document"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="Fahrzeuge">
    <wsdl:port name="FahrzeugeSoap" binding="tns:FahrzeugeSoap">
      <soap:address location="{address}"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""


class _SilentServer:
    """Accepts every TCP connection, reads nothing, answers nothing — the
    provider that is "up" at the socket level and hung above it. Counts the
    connections it accepted, so a test can see the retry arrive."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.1)
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}/Fahrzeuge.asmx"

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._held.append(conn)  # held open, never read, never answered

    @property
    def accepted(self) -> int:
        return len(self._held)

    def __enter__(self) -> "_SilentServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for conn in self._held:
            conn.close()
        self._sock.close()


def _run_with_watchdog(fn):
    """Runs ``fn`` in a worker thread and returns ``(exception, elapsed)``. A
    call still blocked after the watchdog fails the test instead of hanging
    it; the blocked worker is released when the caller's ``_SilentServer``
    block exits and closes the sockets it holds."""

    pool = ThreadPoolExecutor(max_workers=1)
    started = time.monotonic()
    future = pool.submit(fn)
    try:
        future.result(timeout=_WATCHDOG_SECONDS)
    except FutureTimeoutError:
        pytest.fail(f"the call was still blocked after {_WATCHDOG_SECONDS}s — no timeout applied")
    except Exception as exc:  # noqa: BLE001 - returned to the test to assert on
        return exc, time.monotonic() - started
    finally:
        pool.shutdown(wait=False)
    pytest.fail("the call against a silent server returned instead of raising")


def test_the_default_timeout_is_the_one_resilience_documents():
    assert resilience.DEFAULT_TIMEOUT_SECONDS == 10.0


def test_a_wsdl_fetch_from_a_silent_server_is_aborted_within_the_timeout():
    with _SilentServer() as server:
        exc, elapsed = _run_with_watchdog(lambda: build_zeep_client(server.url + "?wsdl", timeout=_TIMEOUT))

    assert isinstance(exc, ProviderTransportError), exc
    assert "WSDL" in str(exc)
    assert _TIMEOUT <= elapsed < _TIMEOUT + 2.0


def test_a_suchen_call_to_a_silent_server_is_aborted_within_the_timeout_on_both_attempts(
    db_session, monkeypatch, tmp_path
):
    """The whole real path: ``build_zeep_client`` → the adapter's ``_suchen``
    → ``call_with_retry``. Each of the two attempts is cut off by the
    transport's timeout, and the failure surfaces as ``ProviderTransportError``
    with the library's timeout chained underneath."""

    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    connection = _make_connection(db_session, _make_provider(db_session))
    monkeypatch.setattr(
        auto_i_dat_soap,
        "secrets_backend",
        FakeSecretsBackend({(connection.id, "password"): "s3cret", (connection.id, "aes_key"): "0" * 32}),
    )

    with _SilentServer() as server:
        wsdl = tmp_path / "fahrzeuge.wsdl"
        wsdl.write_text(_WSDL.format(address=server.url), encoding="utf-8")
        adapter = AutoIDatSoapAdapter(
            db=db_session,
            connection=connection,
            soap_client=build_zeep_client(wsdl.as_uri(), timeout=_TIMEOUT),
            actor_id=None,
            purpose="vehicle_data",
        )
        exc, elapsed = _run_with_watchdog(lambda: adapter.get_system_watermark())
        accepted = server.accepted

    assert isinstance(exc, ProviderTransportError), exc
    assert "Timeout" in str(exc)  # requests' ReadTimeout, by class name only
    assert accepted == 2  # the first attempt and its one retry — never more
    assert 2 * _TIMEOUT <= elapsed < 2 * _TIMEOUT + 2.0
