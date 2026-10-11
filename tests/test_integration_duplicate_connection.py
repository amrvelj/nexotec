"""KAN-274: a second connection for the same provider and environment is a
409 with the shared error body, never a 500 — for a tenant-scoped and a
platform-scoped connection alike, and when two creates race past the
explicit check (the unique constraint is the backstop).
"""

import logging
import uuid

import pytest

from app.core.errors import ConflictError
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from tests.test_integration_gateway import _make_provider
from tests.test_integration_idempotency import _connection_body, _count, _headers, _manager_token, _platform_admin_token


def _assert_duplicate(response, *, scope: str, tenant_id: uuid.UUID | None, environment: str) -> None:
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"] == {
        "reason": "duplicate_connection",
        "scope": scope,
        "tenantId": str(tenant_id) if tenant_id is not None else None,
        "providerCode": "auto_i_dat_mock",
        "environment": environment,
    }


def test_a_second_tenant_connection_for_one_provider_and_environment_is_a_409(client, db_session, caplog):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _manager_token(tenant_id)
    first = client.post("/v1/integrations/connections", json=_connection_body(provider), headers=_headers(token))
    assert first.status_code == 201, first.text

    with caplog.at_level(logging.ERROR):
        second = client.post(
            "/v1/integrations/connections", json=_connection_body(provider, name="again"), headers=_headers(token)
        )

    _assert_duplicate(second, scope="tenant", tenant_id=tenant_id, environment="sandbox")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id == tenant_id) == 1


def test_another_tenant_or_environment_is_not_a_duplicate(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _manager_token(tenant_id)
    for environment in ("sandbox", "production"):
        response = client.post(
            "/v1/integrations/connections", json=_connection_body(provider, environment), headers=_headers(token)
        )
        assert response.status_code == 201, response.text
    other = client.post(
        "/v1/integrations/connections", json=_connection_body(provider), headers=_headers(_manager_token())
    )
    assert other.status_code == 201, other.text


def test_a_second_platform_connection_for_one_provider_and_environment_is_a_409(client, db_session):
    provider = _make_provider(db_session)
    token = _platform_admin_token()
    body = {**_connection_body(provider, "production"), "scope": "platform"}
    first = client.post("/v1/integrations/connections", json=body, headers=_headers(token))
    assert first.status_code == 201, first.text

    second = client.post("/v1/integrations/connections", json=body, headers=_headers(_platform_admin_token()))

    _assert_duplicate(second, scope="platform", tenant_id=None, environment="production")
    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id.is_(None)) == 1


def test_a_create_racing_past_the_check_is_refused_by_the_constraint_as_the_same_409(db_session, monkeypatch):
    """Two concurrent creates both pass the explicit check; the loser's
    flush hits uq_integration_connection_tenant_provider_env. Simulated by
    hiding the first row from the check."""

    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    data = ConnectionCreate(provider_id=provider.id, display_name="First", environment=ConnectionEnvironment.SANDBOX)
    connection_service.create_connection(db_session, tenant_id=tenant_id, data=data, actor_id=uuid.uuid4())

    real_scalar = db_session.scalar

    def scalar_missing_the_duplicate(statement, *args, **kwargs):
        if "integration_connection.id" in str(statement) and "environment" in str(statement):
            return None
        return real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", scalar_missing_the_duplicate)
    with pytest.raises(ConflictError) as raised:
        connection_service.create_connection(db_session, tenant_id=tenant_id, data=data, actor_id=uuid.uuid4())
    monkeypatch.undo()

    assert raised.value.details is not None
    assert raised.value.details["reason"] == "duplicate_connection"
    # The savepoint kept the session usable: the caller's transaction was not poisoned.
    assert _count(db_session, IntegrationConnection, IntegrationConnection.tenant_id == tenant_id) == 1
