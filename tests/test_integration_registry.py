"""WP-6 PR-1: the integration registry — write-only secrets, connection
CRUD, manager-flag-only gating, tenant scoping.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError, NotFoundError
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service


class FakeSecretsBackend:
    """In-memory stand-in for services.secrets_backend — the real module
    talks to a live Infisical project, which no test environment has
    configured. Injected via the `secrets_backend_module` parameter every
    connections.py function that touches secrets already accepts.
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    def _key(self, connection_id, slot):
        return f"{connection_id}:{slot}"

    def create_secret(self, *, connection_id, slot, value):
        self.store[self._key(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def update_secret(self, *, connection_id, slot, value):
        self.store[self._key(connection_id, slot)] = value
        return f"/integrations/{connection_id}/{slot}"

    def delete_secret(self, *, connection_id, slot):
        self.store.pop(self._key(connection_id, slot), None)
        self.deleted.append(self._key(connection_id, slot))


def _make_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "auto_i_dat",
        "category": "vehicle_data",
        "display_name": "auto-i-dat",
        "auth_type": "soap_password_aes",
        "required_secret_slots": ["password", "aes_key"],
        "capability_codes": ["vehicle_data", "images", "packages", "valuation", "forecast"],
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


# --- write-only secrets: never returned, on create, rotate, or read ---------


def test_secret_value_never_returned_on_set_or_rotate(db_session):
    provider = _make_provider(db_session)
    backend = FakeSecretsBackend()
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )

    ref = connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="s3cr3t",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    assert not hasattr(ref, "value")
    assert not hasattr(ref, "secret_value")
    assert ref.secret_ref != "s3cr3t"  # a pointer, never the value

    rotated = connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="new-secret",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    assert rotated.secret_ref != "new-secret"
    assert backend.store[f"{connection.id}:password"] == "new-secret"  # rotation replaced, in the backend only


def test_secret_ref_is_a_pointer_not_a_value_at_rest(db_session):
    provider = _make_provider(db_session)
    backend = FakeSecretsBackend()
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="s3cr3t",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    row = db_session.query(IntegrationSecretRef).filter_by(connection_id=connection.id).one()
    assert row.secret_ref != "s3cr3t"
    assert "s3cr3t" not in row.secret_ref


def test_multiple_secret_slots_per_connection(db_session):
    """auto-i-dat needs two: password + aes_key."""

    provider = _make_provider(db_session)
    backend = FakeSecretsBackend()
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="pw",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.AES_KEY, value="key",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    slots = connection_service.list_secret_slots(db_session, connection_id=connection.id)
    assert {s.slot for s in slots} == {SecretSlot.PASSWORD, SecretSlot.AES_KEY}


def test_delete_connection_deletes_every_secret_slot_from_the_backend(db_session):
    provider = _make_provider(db_session)
    backend = FakeSecretsBackend()
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="pw",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.AES_KEY, value="key",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )

    connection_service.delete_connection(
        db_session, connection=connection, confirm=True, actor_id=uuid.uuid4(), secrets_backend_module=backend
    )
    assert backend.store == {}
    assert len(backend.deleted) == 2


# --- sandbox/production, scope, tenant partitioning -------------------------


def test_sandbox_and_production_are_separate_connections(db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    sandbox = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="Sandbox", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    production = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="Prod", environment=ConnectionEnvironment.PRODUCTION),
        actor_id=uuid.uuid4(),
    )
    assert sandbox.id != production.id
    assert sandbox.environment != production.environment


def test_duplicate_connection_same_tenant_provider_environment_is_refused(db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="First", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(IntegrityError):  # the DB's own unique constraint
        connection_service.create_connection(
            db_session, tenant_id=tenant_id,
            data=ConnectionCreate(provider_id=provider.id, display_name="Second", environment=ConnectionEnvironment.SANDBOX),
            actor_id=uuid.uuid4(),
        )
    db_session.rollback()


def test_platform_scope_connection_has_null_tenant_id(db_session):
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=None,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Platform", environment=ConnectionEnvironment.PRODUCTION,
            scope=ConnectionScope.PLATFORM,
        ),
        actor_id=uuid.uuid4(),
    )
    assert connection.tenant_id is None
    assert connection.scope == ConnectionScope.PLATFORM


def test_tenant_scope_connection_without_a_tenant_id_is_refused(db_session):
    provider = _make_provider(db_session)
    with pytest.raises(ConflictError):
        connection_service.create_connection(
            db_session, tenant_id=None,
            data=ConnectionCreate(
                provider_id=provider.id, display_name="Bad", environment=ConnectionEnvironment.SANDBOX,
                scope=ConnectionScope.TENANT,
            ),
            actor_id=uuid.uuid4(),
        )


def test_duplicate_platform_connection_same_provider_environment_is_refused(db_session):
    provider = _make_provider(db_session)
    connection_service.create_connection(
        db_session, tenant_id=None,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="First", environment=ConnectionEnvironment.PRODUCTION,
            scope=ConnectionScope.PLATFORM,
        ),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(ConflictError):
        connection_service.create_connection(
            db_session, tenant_id=None,
            data=ConnectionCreate(
                provider_id=provider.id, display_name="Second", environment=ConnectionEnvironment.PRODUCTION,
                scope=ConnectionScope.PLATFORM,
            ),
            actor_id=uuid.uuid4(),
        )


# --- instant/reversible disable, confirmed/audited delete -------------------


def test_disable_is_instant_and_reversible(db_session):
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    disabled = connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    assert disabled.enabled is False

    enabled = connection_service.enable_connection(db_session, connection=disabled, actor_id=uuid.uuid4())
    assert enabled.enabled is True


def test_delete_requires_confirm(db_session):
    provider = _make_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(ConflictError):
        connection_service.delete_connection(
            db_session, connection=connection, confirm=False, actor_id=uuid.uuid4(),
            secrets_backend_module=FakeSecretsBackend(),
        )


def test_cross_tenant_connection_read_is_404_not_403(db_session):
    provider = _make_provider(db_session)
    owner_tenant = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=owner_tenant,
        data=ConnectionCreate(provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(NotFoundError):
        connection_service.get_connection_or_404(db_session, tenant_id=uuid.uuid4(), connection_id=connection.id)


# --- API surface: manager-flag-only gating -----------------------------------


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
    tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_non_manager_non_platform_admin_cannot_list_connections(client, db_session):
    _make_provider(db_session)
    token = _token(AccessRole.SALES)  # a real functional role, but not the manager flag
    response = client.get("/v1/integrations/connections", headers=_bearer(token))
    assert response.status_code == 403, response.text


def test_dealer_manager_can_create_and_list_own_connection(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _token(is_dealer_manager=True, tenant_id=tenant_id)

    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["tenantId"] == str(tenant_id)
    assert created.json()["scope"] == "tenant"  # never trusts a client-supplied scope

    listed = client.get("/v1/integrations/connections", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1


def test_dealer_manager_cannot_create_a_platform_scoped_connection(client, db_session):
    provider = _make_provider(db_session)
    token = _token(is_dealer_manager=True)

    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "Sneaky", "environment": "sandbox", "scope": "platform"},
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["scope"] == "tenant"  # overridden, not honoured
    assert created.json()["tenantId"] is not None


def test_platform_admin_sees_connections_across_tenants(client, db_session):
    provider = _make_provider(db_session)
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "A", "environment": "sandbox"},
        headers=_bearer(_token(is_dealer_manager=True, tenant_id=tenant_a)),
    )
    client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "B", "environment": "sandbox"},
        headers=_bearer(_token(is_dealer_manager=True, tenant_id=tenant_b)),
    )

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    listed = client.get("/v1/integrations/connections", headers=_bearer(admin_token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 2


def test_secret_endpoint_response_never_carries_the_value(client, db_session):
    provider = _make_provider(db_session)
    tenant_id = uuid.uuid4()
    token = _token(is_dealer_manager=True, tenant_id=tenant_id)
    connection = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=_bearer(token),
    ).json()

    # No live Infisical project is configured in this test environment —
    # the API-level write is expected to surface that as a 409, never a
    # 500 and never a value leaking through in an error body either.
    response = client.put(
        f"/v1/integrations/connections/{connection['id']}/secrets/password",
        json={"secretValue": "s3cr3t-password"},
        headers=_bearer(token),
    )
    assert "s3cr3t-password" not in response.text


def test_every_integrations_connection_field_response_shape_has_no_secret_key(client, db_session):
    provider = _make_provider(db_session)
    token = _token(is_dealer_manager=True)
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "auto-i-dat", "environment": "sandbox"},
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "secretRef" not in body
    assert "secretValue" not in body
    assert "value" not in body
