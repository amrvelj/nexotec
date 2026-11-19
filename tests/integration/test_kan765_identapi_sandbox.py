"""KAN-765 — a sandbox connection talks to auto-i-dat's test environment with
the Client Credentials Grant; a production connection stays exactly as it was
(the User Enrollment Grant, Anto's ruling of 2026-11-19).

Everything runs the real, registered `auto_i_dat` adapter against the fake
authorization servers and identAPIs of `tests/identapi_support.py` over
`httpx.MockTransport`: no network, no real value. Each fake authorization
server accepts only its own environment's grant and each fake identAPI only
its own authority's tokens, so a request sent to the wrong environment fails
here. Nothing in this file has run against the real test environment.

The production behaviour these tests contrast with is pinned, unchanged, by
the KAN-488 … KAN-588 tests (`test_integration_identapi_foundation.py`,
`test_integration_identapi_user_quota.py`, …), which run on a production
connection since this ticket.
"""

import uuid

import pytest

from app.core.audit import AuditEvent
from app.integration.adapters import autoi_oauth
from app.integration.adapters.autoi_oauth import (
    ENVIRONMENTS,
    OAUTH_DISCOVERY_OPERATION,
    OAUTH_TOKEN_OPERATION,
    Grant,
)
from app.integration.errors import ProviderRejectedError, ProviderUserQuotaExceededError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, ConnectionStatus
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import gateway, resilience
from tests import identapi_support as fx
from tests.identapi_support import (
    PROBE_PATHS,
    PRODUCTION_API,
    PRODUCTION_AUTHORITY,
    SANDBOX_API,
    SANDBOX_AUTHORITY,
    FakeAutoi,
)

SANDBOX = ConnectionEnvironment.SANDBOX
PRODUCTION = ConnectionEnvironment.PRODUCTION
_WELL_KNOWN = "/.well-known/oauth-authorization-server"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _rows(db_session, connection, operation: str | None = None) -> list[IntegrationCallLog]:
    query = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id)
    if operation is not None:
        query = query.filter_by(capability=operation)
    return query.order_by(IntegrationCallLog.created_at, IntegrationCallLog.id).all()


# --- exit criterion 1: endpoints and grant by environment -------------------------------


def test_the_provider_constants_map_each_environment_to_its_hosts_and_grant():
    assert set(ENVIRONMENTS) == set(ConnectionEnvironment)
    sandbox, production = ENVIRONMENTS[SANDBOX], ENVIRONMENTS[PRODUCTION]
    assert (sandbox.authority, sandbox.api_base_url, sandbox.grant) == (
        "https://www.test.autoi.ch", "https://api.ident.test.autoi.ch", Grant.CLIENT_CREDENTIALS,
    )
    assert (production.authority, production.api_base_url, production.grant) == (
        "https://www.autoi.ch", "https://api.ident.autoi.ch", Grant.USER_ENROLLMENT,
    )
    assert sandbox.discovery_url == "https://www.test.autoi.ch/.well-known/oauth-authorization-server"
    assert production.discovery_url == "https://www.autoi.ch/.well-known/oauth-authorization-server"


def test_a_sandbox_connection_sends_every_request_to_the_test_environment(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error
    hosts = {(r.host, r.path) for r in s.fake.seen}
    assert (SANDBOX_AUTHORITY, _WELL_KNOWN) in hosts
    assert (SANDBOX_AUTHORITY, "/api/oauth2/token") in hosts
    assert {r.host for r in s.fake.seen} == {SANDBOX_AUTHORITY, SANDBOX_API}
    # Discovery first; the token went where the test authority's metadata said.
    assert (s.fake.seen[0].host, s.fake.seen[0].path) == (SANDBOX_AUTHORITY, _WELL_KNOWN)
    assert {r.path for r in s.fake.api_requests()} == {"/caption/code", *PROBE_PATHS.values()}
    assert {r.host for r in s.fake.api_requests()} == {SANDBOX_API}


def test_a_production_connection_still_sends_every_request_to_production(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=PRODUCTION)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error
    assert {r.host for r in s.fake.seen} == {PRODUCTION_AUTHORITY, PRODUCTION_API}
    (token_request,) = s.fake.token_requests()
    assert token_request.form["grant_type"] == "user_enrollment"
    assert token_request.form["user_id"] == str(s.user.id)


def test_the_discovery_cache_keeps_the_two_authorities_apart(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=PRODUCTION)
    sandbox = fx.make_connection(
        db_session, fx.make_provider(db_session, provider_code="auto_i_dat_second"), tenant_id=s.connection.tenant_id,
        secrets=s.secrets, environment=SANDBOX,
    )
    # The second provider row is only there so both connections may live in
    # one Betrieb; the adapter is the identAPI's either way.
    monkeypatch.setitem(gateway._ADAPTER_FACTORIES, "auto_i_dat_second", gateway._ADAPTER_FACTORIES["auto_i_dat"])

    for _ in range(2):
        assert gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id).status == (
            ConnectionStatus.CONNECTED
        )
        assert gateway.test_connection(db_session, connection=sandbox, actor_id=s.user.id).status == (
            ConnectionStatus.CONNECTED
        )

    discoveries = [r.host for r in s.fake.seen if r.path == _WELL_KNOWN]
    assert sorted(discoveries) == sorted([PRODUCTION_AUTHORITY, SANDBOX_AUTHORITY])  # once each, then cached
    assert set(autoi_oauth._discovery_cache) == {
        ENVIRONMENTS[PRODUCTION].discovery_url, ENVIRONMENTS[SANDBOX].discovery_url,
    }
    # Each token was requested at its own authority's endpoint.
    assert sorted(r.host for r in s.fake.token_requests()) == sorted([PRODUCTION_AUTHORITY, SANDBOX_AUTHORITY])


# --- exit criterion 2: the sandbox login -----------------------------------------------------


def test_the_sandbox_login_is_the_client_credentials_grant_and_nothing_else(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    (token_request,) = s.fake.token_requests()
    assert token_request.host == SANDBOX_AUTHORITY
    assert token_request.form == {"grant_type": "client_credentials", "scope": "ident"}
    assert token_request.headers["authorization"] == autoi_oauth.basic_authorization(fx.CLIENT_ID, fx.CLIENT_SECRET)
    assert token_request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "accept-language" not in token_request.headers
    # No user datum anywhere in the login, header or body.
    sent = " ".join([*token_request.headers.values(), *token_request.form.values()])
    for value in (str(s.user.id), s.user.email, s.user.first_name, s.user.last_name):
        assert value not in sent
    for request in s.fake.api_requests():
        assert request.headers["authorization"] == f"Bearer {s.fake.tokens_issued[0]}"


def test_the_sandbox_discovery_does_not_check_grant_types_supported(db_session, monkeypatch):
    """auto-i's own spec documents that the live document lists no
    `client_credentials`; the fake's lists only other grants."""

    fake = FakeAutoi()
    assert "client_credentials" not in fake.discovery["grant_types_supported"]
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=SANDBOX)

    assert gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id).status == (
        ConnectionStatus.CONNECTED
    )


def test_the_sandbox_discovery_keeps_the_client_authentication_check(db_session, monkeypatch):
    fake = FakeAutoi()
    fake.discovery["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=SANDBOX)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "client_secret_basic" in (updated.last_error or "")
    assert s.fake.token_requests() == []


def test_the_sandbox_discovery_keeps_the_scope_check(db_session, monkeypatch):
    fake = FakeAutoi()
    fake.discovery["scopes_supported"] = ["openid"]
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=SANDBOX)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "'ident' scope" in (updated.last_error or "")
    assert s.fake.token_requests() == []


# --- exit criterion 3: no user on a sandbox call -------------------------------------------


def test_two_users_share_one_sandbox_token(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    other = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="bob.tester@example.test", first_name="Bob")

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)

    assert len(s.fake.token_requests()) == 1
    assert [key[2] for key in autoi_oauth._token_cache] == [None]
    assert {r.headers["authorization"] for r in s.fake.api_requests()} == {f"Bearer {s.fake.tokens_issued[0]}"}


def test_the_sandbox_token_is_left_behind_by_a_rotation(db_session, monkeypatch):
    """The cache key is still the connection's `version` and `rotated_at`."""

    from app.integration.services import connections as connection_service

    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    connection_service.set_secret(
        db_session, connection=s.connection, slot=SecretSlot.CLIENT_SECRET, value=fx.CLIENT_SECRET,
        actor_id=s.user.id, secrets_backend_module=s.secrets,
    )

    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert len(s.fake.token_requests()) == 2


def test_a_sandbox_call_with_no_signed_in_user_works(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=None)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error
    assert len(s.fake.token_requests()) == 1
    # The secret resolution is still audited — with no actor to name.
    (resolution,) = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="integration_secret_ref", entity_id=s.connection.id, action="secret_resolved")
        .all()
    )
    assert resolution.actor_id is None


def test_a_sandbox_call_by_a_user_outside_the_betrieb_works_and_audits_them(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    staff = fx.make_user(db_session, tenant_id=uuid.uuid4(), email="eve.elsewhere@example.test", first_name="Eve")

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=staff.id)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error
    (resolution,) = (
        db_session.query(AuditEvent)
        .filter_by(entity_type="integration_secret_ref", entity_id=s.connection.id, action="secret_resolved")
        .all()
    )
    assert resolution.actor_id == staff.id


def test_a_sandbox_connection_without_a_betrieb_is_not_refused(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    s.connection.scope = ConnectionScope.PLATFORM
    s.connection.tenant_id = None
    db_session.commit()

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error


def test_a_production_call_with_no_signed_in_user_is_still_refused_before_anything_is_sent(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=PRODUCTION)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=None)

    assert updated.status == ConnectionStatus.ERROR
    assert "signed-in user" in (updated.last_error or "")
    assert s.fake.seen == []


# --- exit criterion 4: failure behaviour is the same in both environments -------------------


def test_a_wrong_sandbox_secret_is_a_refused_login_that_charges_the_breaker(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    s.secrets.values[(s.connection.id, "client_secret")] = "not-the-secret"
    charged: list[uuid.UUID] = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert "token endpoint" in (updated.last_error or "") and "401" in (updated.last_error or "")
    assert "not-the-secret" not in (updated.last_error or "")
    assert charged == [s.connection.id]
    assert s.fake.api_requests() == []
    assert [(r.capability, r.status, r.cost_units) for r in _rows(db_session, s.connection)] == [
        (OAUTH_DISCOVERY_OPERATION, CallStatus.SUCCESS, 0),
        (OAUTH_TOKEN_OPERATION, CallStatus.ERROR, 0),
    ]


def test_a_sandbox_400_is_a_plain_rejection_never_a_user_quota(db_session, monkeypatch):
    """The fake answers a 400 with `user_quota_exceeded` — production's own
    answer (KAN-586). A client credentials login enrols nobody, so on a
    sandbox connection it is a refused login naming the endpoint's status."""

    fake = FakeAutoi(token_status=400)
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=SANDBOX)

    with pytest.raises(ProviderRejectedError) as raised, gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id
    ) as adapter:
        adapter.fetch_code_captions("de")

    assert not isinstance(raised.value, ProviderUserQuotaExceededError)
    assert "HTTP 400" in str(raised.value)
    assert "user quota" not in str(raised.value)


def test_a_production_400_user_quota_is_still_named_as_such(db_session, monkeypatch):
    fake = FakeAutoi(token_status=400)
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=PRODUCTION)

    with pytest.raises(ProviderUserQuotaExceededError), gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id
    ) as adapter:
        adapter.fetch_code_captions("de")


def test_a_sandbox_token_endpoint_5xx_is_retried_once_then_charged(db_session, monkeypatch):
    fake = FakeAutoi(token_status=503)
    s = fx.connected(db_session, monkeypatch, fake=fake, environment=SANDBOX)
    charged: list[uuid.UUID] = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)

    assert updated.status == ConnectionStatus.ERROR
    assert len(s.fake.token_requests()) == 2
    assert [r.status for r in _rows(db_session, s.connection, OAUTH_TOKEN_OPERATION)] == [CallStatus.ERROR] * 2
    assert charged == [s.connection.id]


def test_a_401_from_the_sandbox_api_drops_the_shared_token_once_and_retries(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    other = fx.make_user(db_session, tenant_id=s.connection.tenant_id, email="bob.tester@example.test", first_name="Bob")
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    assert len(s.fake.token_requests()) == 1

    s.fake.expire_tokens_once = True
    updated = gateway.test_connection(db_session, connection=s.connection, actor_id=other.id)

    assert updated.status == ConnectionStatus.CONNECTED, updated.last_error
    assert len(s.fake.token_requests()) == 2
    # The fresh token is the connection's again, whoever asks next.
    gateway.test_connection(db_session, connection=s.connection, actor_id=s.user.id)
    assert len(s.fake.token_requests()) == 2


# --- rule 7 is untouched ------------------------------------------------------------------------


def test_a_sandbox_connection_is_still_refused_on_a_production_deployment(db_session, monkeypatch):
    from app.core.config import get_settings

    s = fx.connected(db_session, monkeypatch, environment=SANDBOX)
    monkeypatch.setattr(get_settings(), "environment", "production")

    with pytest.raises(gateway.SandboxConnectionOnProductionError), gateway.call_capability(
        db_session, connection=s.connection, capability="vehicle_data", actor_id=s.user.id
    ) as adapter:
        adapter.fetch_code_captions("de")

    assert s.fake.seen == []
