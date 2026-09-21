"""`POST /v1/integrations/connections/{id}/test` must never 500 because a provider misbehaved.

The route's own docstring promises it: "a dependency being down degrades this
one connection's own status, it never raises past this endpoint as a 500."
`gateway.test_connection` only caught `ProviderGatewayError`, and none of the
failures a REAL adapter raises were one — an empty response (which is what a
wrong Benutzername/Passwort looks like, the first thing a dealer configuring a
real account hits), a maintenance window, a transport error, a WSDL that will
not load, an open circuit. The mock adapter never raises any of them, so the
hole stayed hidden behind a test that only exercised `UnknownProviderError`.

So these tests drive the REAL `AutoIDatSoapAdapter` against a fake `Suchen`
and assert on what the dealer sees — the connection's own row and the HTTP
status — not on which exception class the adapter happens to raise.
"""

import sys
import types
import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.integration.adapters import auto_i_dat_soap
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, resilience
from app.main import app as fastapi_app
from tests.test_integration_soap_adapter import _AES_KEY, FakeSecretsBackend, FakeSoapClient

_PASSWORD = "s3cret"


# --- the fake provider -----------------------------------------------------------------


class _EmptyStringClient:
    """`Suchen` -> "". Webservice Fahrzeuge p4: an empty string means an
    invalid Benutzername / Passwort / Sprache / Datenname."""

    def Suchen(self, **kwargs):
        return ""


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def Suchen(self, **kwargs):
        self.calls += 1
        raise self._exc


_MAINTENANCE_XML = (
    "<AutoiSystem><Info><Status>1</Status><StatusMsg>Webservice ist offline (Wartung)</StatusMsg></Info></AutoiSystem>"
)


# --- arranging a connection that speaks through the real adapter -------------------------


def _provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat",
        category="vehicle_data",
        display_name="auto-i-dat",
        auth_type="soap_password_aes",
        required_secret_slots=["password", "aes_key"],
        capability_codes=["fahrzeuge"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _connection(db_session, tenant_id: uuid.UUID, *, config: dict | None = None) -> IntegrationConnection:
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=_provider(db_session).id,
            display_name="auto-i-dat",
            environment=ConnectionEnvironment.SANDBOX,
            config=config or {},
        ),
        actor_id=uuid.uuid4(),
    )


def _serve(db_session, monkeypatch, tenant_id, soap_client, *, aes_key: str | None = None) -> IntegrationConnection:
    """A real `AutoIDatSoapAdapter` in front of `soap_client`, with the
    secrets manager replaced by an in-memory one."""

    connection = _connection(db_session, tenant_id, config={"username": "dealer"})
    monkeypatch.setattr(
        auto_i_dat_soap,
        "secrets_backend",
        FakeSecretsBackend(
            {
                (connection.id, "password"): _PASSWORD,
                (connection.id, "aes_key"): aes_key if aes_key is not None else _AES_KEY.decode("ascii"),
            }
        ),
    )
    monkeypatch.setitem(
        gateway._ADAPTER_FACTORIES,
        "auto_i_dat",
        lambda db, conn, actor_id, purpose: AutoIDatSoapAdapter(
            db=db, connection=conn, soap_client=soap_client, actor_id=actor_id, purpose=purpose
        ),
    )
    return connection


# One builder per way a real provider connection can fail, each paired with the
# fragment the dealer must be able to read in `lastError`. Both the service-level
# and the HTTP-level tests below run every entry, so a new failure mode added
# here is covered at both layers.


def _wrong_password(db_session, monkeypatch, tenant_id):
    return _serve(db_session, monkeypatch, tenant_id, _EmptyStringClient())


def _maintenance(db_session, monkeypatch, tenant_id):
    return _serve(db_session, monkeypatch, tenant_id, FakeSoapClient(responses={"System": _MAINTENANCE_XML}))


def _unusable_response(db_session, monkeypatch, tenant_id):
    return _serve(db_session, monkeypatch, tenant_id, FakeSoapClient(responses={"System": "this is not xml"}))


def _transport_error(db_session, monkeypatch, tenant_id):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    return _serve(db_session, monkeypatch, tenant_id, _RaisingClient(ConnectionError("connection refused")))


def _truncated_aes_key(db_session, monkeypatch, tenant_id):
    # A dealer pasted five characters of the key: not a valid AES length.
    return _serve(db_session, monkeypatch, tenant_id, FakeSoapClient(), aes_key="short")


class _ZeepStyleTransportError(Exception):
    """Stands in for `zeep.exceptions.TransportError`: a library error that is
    neither an `OSError` nor anything of ours."""


def _wsdl_will_not_load(db_session, monkeypatch, tenant_id):
    def _fails(wsdl_url, *args, **kwargs):
        raise _ZeepStyleTransportError("Server returned HTTP status 404")

    # `zeep` is imported lazily by `build_zeep_client` and is not necessarily
    # installed where the fast lane runs — a stub module works either way.
    stub = types.ModuleType("zeep")
    stub.Client = _fails  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "zeep", stub)
    # No factory override: this is the real, registered `_build_real_adapter`.
    return _connection(db_session, tenant_id, config={"wsdlUrl": "https://wsdl.example.test/x?wsdl"})


def _missing_password(db_session, monkeypatch, tenant_id):
    # Created and tested before the password slot was ever set: the secrets
    # manager has nothing to give (`FakeSecretsBackend` raises, as the real SDK does).
    connection = _serve(db_session, monkeypatch, tenant_id, FakeSoapClient())
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", FakeSecretsBackend({}))
    return connection


def _no_wsdl_url(db_session, monkeypatch, tenant_id):
    # A freshly created real connection, tested before anything is filled in.
    return _connection(db_session, tenant_id)


def _circuit_open(db_session, monkeypatch, tenant_id):
    connection = _serve(db_session, monkeypatch, tenant_id, FakeSoapClient())
    for _ in range(resilience._FAILURE_THRESHOLD):
        resilience.record_failure(connection.id)
    return connection


_SCENARIOS: dict[str, tuple[Callable, str]] = {
    "wrong_password": (_wrong_password, "Passwort"),
    "maintenance": (_maintenance, "Wartung"),
    "unusable_response": (_unusable_response, "not well-formed"),
    "transport_error": (_transport_error, "ConnectionError"),
    "truncated_aes_key": (_truncated_aes_key, "decrypt"),
    "wsdl_will_not_load": (_wsdl_will_not_load, "WSDL"),
    "missing_password": (_missing_password, "'password' credential"),
    "no_wsdl_url": (_no_wsdl_url, "wsdlUrl"),
    "circuit_open": (_circuit_open, "Circuit breaker"),
}


@pytest.fixture(autouse=True)
def _reset_circuits():
    yield
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


# --- through gateway.test_connection ---------------------------------------------------


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_a_provider_failure_marks_the_connection_error_instead_of_raising(db_session, monkeypatch, scenario):
    build, expected = _SCENARIOS[scenario]
    connection = build(db_session, monkeypatch, uuid.uuid4())

    updated = gateway.test_connection(db_session, connection=connection)

    assert updated.status == ConnectionStatus.ERROR
    assert expected in (updated.last_error or ""), updated.last_error
    assert updated.last_verified_at is None  # a failed test never stamps "verified"


def test_a_wrong_password_is_a_failed_call_and_never_reaches_entitlement_probing(db_session, monkeypatch):
    """Probing runs only after `System` succeeded — that ordering is what makes
    a probe's rejection attributable to the Datenname. A rejected `System` must
    therefore end the test with exactly one logged, failed call."""

    connection = _wrong_password(db_session, monkeypatch, uuid.uuid4())

    gateway.test_connection(db_session, connection=connection)

    calls = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert [(c.capability, c.status) for c in calls] == [("system_watermark", CallStatus.ERROR)]
    assert connection_service.list_entitlements(db_session, connection_id=connection.id) == []


def test_last_error_never_echoes_a_foreign_exception_message(db_session, monkeypatch):
    """`last_error` is readable by every dealer manager. A foreign library's
    message can carry a URL, a header, or a server's echo of what it was sent —
    so only the class name of a foreign exception may reach it."""

    leaky = ConnectionError(f"HTTPSConnectionPool(host='dealer:{_PASSWORD}@wsdl.example.test', port=443)")
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    connection = _serve(db_session, monkeypatch, uuid.uuid4(), _RaisingClient(leaky))

    updated = gateway.test_connection(db_session, connection=connection)

    assert updated.status == ConnectionStatus.ERROR
    assert updated.last_error is not None
    assert "ConnectionError" in updated.last_error
    assert _PASSWORD not in updated.last_error
    assert "wsdl.example.test" not in updated.last_error


def test_a_programmer_error_is_not_dressed_up_as_a_connection_failure(db_session, monkeypatch):
    """The whole point of translating at the adapter boundary rather than
    catching `Exception` in the gateway: a bug must stay loud."""

    class _BuggyAdapter(MockAutoIDatAdapter):
        def get_system_watermark(self):
            raise AttributeError("no such method")

    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    connection = connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="mock", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )
    monkeypatch.setitem(
        gateway._ADAPTER_FACTORIES, "auto_i_dat_mock", lambda db, conn, actor_id, purpose: _BuggyAdapter()
    )

    with pytest.raises(AttributeError):
        gateway.test_connection(db_session, connection=connection)

    db_session.refresh(connection)
    assert connection.status == ConnectionStatus.NOT_CONFIGURED  # untouched


# --- through the HTTP endpoint ----------------------------------------------------------


def _bearer(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(),
        is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_the_test_endpoint_answers_200_with_an_error_status_never_500(client, db_session, monkeypatch, scenario):
    build, expected = _SCENARIOS[scenario]
    tenant_id = uuid.uuid4()
    connection = build(db_session, monkeypatch, tenant_id)
    # The shared `client` fixture re-raises server exceptions into the test;
    # this one turns an unhandled exception into the 500 a dealer would see.
    http = TestClient(fastapi_app, raise_server_exceptions=False)

    response = http.post(f"/v1/integrations/connections/{connection.id}/test", headers=_bearer(tenant_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    assert expected in body["lastError"], body["lastError"]


def test_testing_a_marketplace_connection_is_refused_cleanly_not_a_500(client, db_session):
    """The dealer view offers "Test" on every connection card. A marketplace
    adapter has no `System` probe, so this used to be an `AttributeError` and an
    HTTP 500 — and, had it been caught as a failed call, would have marked a
    healthy AutoScout24 connection `ERROR` and charged its circuit breaker for a
    call that never happened."""

    provider = IntegrationProvider(
        provider_code="autoscout24",
        category="marketplace",
        display_name="AutoScout24",
        auth_type="ftp_password",
        required_secret_slots=["password"],
        capability_codes=["marketplace_publish"],
    )
    db_session.add(provider)
    db_session.commit()
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session,
        tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id,
            display_name="AutoScout24",
            environment=ConnectionEnvironment.SANDBOX,
            config={"ftpHost": "ftp.example.test", "ftpUsername": "dealer", "kundennummer": "1"},
        ),
        actor_id=uuid.uuid4(),
    )
    http = TestClient(fastapi_app, raise_server_exceptions=False)

    response = http.post(f"/v1/integrations/connections/{connection.id}/test", headers=_bearer(tenant_id))

    assert response.status_code == 409, response.text
    assert "AutoScout24" in response.json()["error"]["message"]
    db_session.refresh(connection)
    assert connection.status == ConnectionStatus.NOT_CONFIGURED  # untouched
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 0
