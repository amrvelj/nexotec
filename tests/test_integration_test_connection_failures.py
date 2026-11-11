"""`POST /v1/integrations/connections/{id}/test` must never 500 because a provider misbehaved.

The route's own docstring promises it: "a dependency being down degrades this
one connection's own status, it never raises past this endpoint as a 500."
`gateway.test_connection` only catches `ProviderGatewayError`, so every way the
real adapter can fail has to end as one of those — and the dealer has to be
able to read what happened in `lastError`.

KAN-488: the real adapter is the identAPI's (`adapters/auto_i_dat_ident.py`):
OAuth 2.0 discovery and User Enrollment Grant against `www.autoi.ch`, json:api
calls against `api.ident.autoi.ch`. These tests drive it against the fake hosts
in `tests/identapi_support.py` and assert on what the dealer sees — the
connection's own row and the HTTP status — not on which exception class the
adapter happens to raise.
"""

import uuid
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.autoi_oauth import OAUTH_DISCOVERY_OPERATION, OAUTH_TOKEN_OPERATION
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, resilience
from app.main import app as fastapi_app
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi
from tests.token_support import dealer_group_of

# One builder per way a real provider connection can fail, each paired with the
# fragment the dealer must be able to read in `lastError`. Both the service-level
# and the HTTP-level tests below run every entry, so a new failure mode added
# here is covered at both layers. Each returns the Setup (connection + user).


def _wrong_secret(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"
    return s


def _user_quota_exceeded(db_session, monkeypatch):
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(token_status=400))


def _authorization_server_down(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(discovery_status=503))


def _api_down(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={"/caption/code": 500}))


def _transport_error(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(raise_on_api=httpx.ConnectError("connection refused")))


def _timeout(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(raise_on_api=httpx.ReadTimeout("read timed out")))


def _not_entitled_to_captions(db_session, monkeypatch):
    # The access check itself refused: a licence that covers nothing readable.
    return fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={"/caption/code": 403}))


def _discovery_without_basic_auth(db_session, monkeypatch):
    fake = FakeAutoi(discovery={**fx.DISCOVERY, "token_endpoint_auth_methods_supported": ["private_key_jwt"]})
    return fx.connected(db_session, monkeypatch, fake=fake)


def _missing_secret(db_session, monkeypatch):
    # Created and tested before the secret was set: the secrets manager has
    # nothing to give (`FakeSecretsBackend` raises, as the real SDK does).
    s = fx.connected(db_session, monkeypatch)
    s.secrets.values.clear()
    return s


def _missing_client_id(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    s.connection.config = {}
    db_session.commit()
    return s


def _circuit_open(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    for _ in range(resilience._FAILURE_THRESHOLD):
        resilience.record_failure(s.connection.id)
    return s


_SCENARIOS: dict[str, tuple[Callable, str]] = {
    "wrong_secret": (_wrong_secret, "401"),
    "user_quota_exceeded": (_user_quota_exceeded, "user quota"),
    "authorization_server_down": (_authorization_server_down, "HTTP 503"),
    "api_down": (_api_down, "HTTP 500"),
    "transport_error": (_transport_error, "ConnectError"),
    "timeout": (_timeout, "ReadTimeout"),
    "not_entitled_to_captions": (_not_entitled_to_captions, "403"),
    "discovery_without_basic_auth": (_discovery_without_basic_auth, "client_secret_basic"),
    "missing_secret": (_missing_secret, "'client_secret' credential"),
    "missing_client_id": (_missing_client_id, "clientId"),
    "circuit_open": (_circuit_open, "Circuit breaker"),
}


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


# --- through gateway.test_connection ---------------------------------------------------


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_a_provider_failure_marks_the_connection_error_instead_of_raising(db_session, monkeypatch, scenario):
    build, expected = _SCENARIOS[scenario]
    s = build(db_session, monkeypatch)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert expected in (updated.last_error or ""), updated.last_error
    assert updated.last_verified_at is None  # a failed test never stamps "verified"


def test_a_wrong_secret_is_a_failed_login_and_never_reaches_entitlement_probing(db_session, monkeypatch):
    """Probing runs only after the access check succeeded. A refused login must
    therefore end the test with the discovery row, one logged, failed
    `oauth_token` row and no identAPI request at all."""

    s = _wrong_secret(db_session, monkeypatch)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    calls = (
        db_session.query(IntegrationCallLog).filter_by(connection_id=s.connection.id)
        .order_by(IntegrationCallLog.created_at, IntegrationCallLog.id).all()
    )
    assert [(c.capability, c.status) for c in calls] == [
        (OAUTH_DISCOVERY_OPERATION, CallStatus.SUCCESS), (OAUTH_TOKEN_OPERATION, CallStatus.ERROR),
    ]
    assert s.fake.api_requests() == []
    assert connection_service.list_entitlements(db_session, connection_id=s.connection.id) == []


def test_last_error_never_echoes_a_foreign_exception_message(db_session, monkeypatch):
    """`last_error` is readable by every dealer manager. A foreign library's
    message can carry a URL, a header, or a server's echo of what it was sent —
    so only the class name of a foreign exception may reach it."""

    leaky = httpx.ConnectError(f"ConnectionPool(host='client:{fx.CLIENT_SECRET}@api.ident.autoi.ch', port=443)")
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(raise_on_api=leaky))

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert updated.last_error is not None
    assert "ConnectError" in updated.last_error
    assert fx.CLIENT_SECRET not in updated.last_error
    assert "api.ident.autoi.ch" not in updated.last_error


def test_a_programmer_error_is_not_dressed_up_as_a_connection_failure(db_session, monkeypatch):
    """The whole point of translating at the adapter boundary rather than
    catching `Exception` in the gateway: a bug must stay loud."""

    class _BuggyAdapter(MockAutoIDatAdapter):
        def fetch_captions(self, lang):
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



def test_an_access_check_that_metered_no_request_is_logged_as_access_check(db_session, monkeypatch):
    """KAN-600: the block's own row — written only when the access check
    failed before metering a request — is labelled `access_check`, not the
    retired SOAP watermark's `system_watermark`."""

    from sqlalchemy import select

    from app.integration.errors import ProviderTransportError

    class _UnreachableAdapter(MockAutoIDatAdapter):
        def fetch_captions(self, lang):
            raise ProviderTransportError("connection refused")

    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
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
        gateway._ADAPTER_FACTORIES, "auto_i_dat_mock", lambda db, conn, actor_id, purpose: _UnreachableAdapter()
    )

    gateway.test_connection(db_session, connection=connection)

    db_session.refresh(connection)
    assert connection.status == ConnectionStatus.ERROR
    labels = db_session.scalars(
        select(IntegrationCallLog.capability).where(IntegrationCallLog.connection_id == connection.id)
    ).all()
    assert labels == ["access_check"]


# --- through the HTTP endpoint ----------------------------------------------------------


def _bearer(s: fx.Setup) -> dict[str, str]:
    token = create_access_token(
        user_id=s.user.id,
        tenant_id=s.connection.tenant_id,
        group_id=dealer_group_of(str(s.connection.tenant_id)),
        roles=frozenset(),
        is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_the_test_endpoint_answers_200_with_an_error_status_never_500(client, db_session, monkeypatch, scenario):
    build, expected = _SCENARIOS[scenario]
    s = build(db_session, monkeypatch)
    # The shared `client` fixture re-raises server exceptions into the test;
    # this one turns an unhandled exception into the 500 a dealer would see.
    http = TestClient(fastapi_app, raise_server_exceptions=False)

    response = http.post(f"/v1/integrations/connections/{s.connection.id}/test", headers=_bearer(s))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    assert expected in body["lastError"], body["lastError"]


def test_testing_a_marketplace_connection_is_refused_cleanly_not_a_500(client, db_session):
    """The dealer view offers "Test" on every connection card. A marketplace
    adapter has no access check, so this used to be an `AttributeError` and an
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
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)), roles=frozenset(),
        is_dealer_manager=True,
    )

    response = http.post(
        f"/v1/integrations/connections/{connection.id}/test", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 409, response.text
    assert "AutoScout24" in response.json()["error"]["message"]
    db_session.refresh(connection)
    assert connection.status == ConnectionStatus.NOT_CONFIGURED  # untouched
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 0
