"""KAN-602: a connection stores only the secret slots its provider declares.

`connections.set_secret` (and `PUT /v1/integrations/connections/{id}/secrets/{slot}`)
stored a reference for any `SecretSlot` on any connection: an AutoScout24
connection, which uses only `password`, could be given a `certificate` that no
adapter ever resolves. Anto's ruling (2026-11-10, option A): such a slot is
refused with 422 `secret_slot_not_used_by_provider` before anything reaches the
secrets manager; the DELETE route still clears any slot.
"""

import uuid

import pytest

from app.core.auth import create_access_token
from app.core.errors import UnprocessableEntityError
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import secrets_backend
from tests.token_support import dealer_group_of


class RecordingBackend:
    """Stands in for the secrets manager and records every call made to it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_secret(self, *, connection_id, slot, value):
        self.calls.append(("create", slot))
        return secrets_backend.secret_ref_for(connection_id=connection_id, slot=slot)

    def update_secret(self, *, connection_id, slot, value):
        self.calls.append(("update", slot))
        return secrets_backend.secret_ref_for(connection_id=connection_id, slot=slot)

    def delete_secret(self, *, connection_id, slot):
        self.calls.append(("delete", slot))


@pytest.fixture
def backend(monkeypatch) -> RecordingBackend:
    fake = RecordingBackend()
    for name in ("create_secret", "update_secret", "delete_secret"):
        monkeypatch.setattr(secrets_backend, name, getattr(fake, name))
    return fake


def _provider(db_session, *, slots: list[str]) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="autoscout24",
        category="marketplace",
        display_name="AutoScout24",
        auth_type="password",
        required_secret_slots=slots,
        capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _connection(client, provider: IntegrationProvider, headers) -> str:
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "AutoScout24", "environment": "sandbox"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _refs(db_session, connection_id: str) -> list[SecretSlot]:
    db_session.expire_all()
    rows = db_session.query(IntegrationSecretRef).filter_by(connection_id=uuid.UUID(connection_id)).all()
    return sorted(row.slot for row in rows)


def test_a_slot_the_provider_does_not_use_is_refused_before_the_secrets_manager(client, db_session, backend):
    provider = _provider(db_session, slots=["password"])
    headers = _headers(uuid.uuid4())
    connection_id = _connection(client, provider, headers)

    response = client.put(
        f"/v1/integrations/connections/{connection_id}/secrets/certificate",
        json={"secretValue": "-----BEGIN CERTIFICATE-----"},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "unprocessable_entity"
    assert error["details"] == {
        "reason": "secret_slot_not_used_by_provider", "slot": "certificate", "allowedSlots": ["password"],
    }
    assert "BEGIN CERTIFICATE" not in response.text
    assert backend.calls == []
    assert _refs(db_session, connection_id) == []


def test_a_slot_the_provider_declares_is_stored(client, db_session, backend):
    provider = _provider(db_session, slots=["password"])
    headers = _headers(uuid.uuid4())
    connection_id = _connection(client, provider, headers)

    response = client.put(
        f"/v1/integrations/connections/{connection_id}/secrets/password",
        json={"secretValue": "pw"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert backend.calls == [("create", "password")]
    assert _refs(db_session, connection_id) == [SecretSlot.PASSWORD]


def test_a_provider_without_slots_takes_none(db_session, backend):
    provider = _provider(db_session, slots=[])
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="Demo", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )

    with pytest.raises(UnprocessableEntityError) as raised:
        connection_service.set_secret(
            db_session, connection=connection, slot=SecretSlot.PASSWORD, value="pw", actor_id=uuid.uuid4()
        )

    assert raised.value.details == {"reason": "secret_slot_not_used_by_provider", "slot": "password", "allowedSlots": []}
    assert backend.calls == []


def test_a_stray_slot_stored_before_the_rule_can_still_be_deleted(client, db_session, backend):
    provider = _provider(db_session, slots=["password"])
    headers = _headers(uuid.uuid4())
    connection_id = _connection(client, provider, headers)
    stray = IntegrationSecretRef(
        connection_id=uuid.UUID(connection_id), slot=SecretSlot.CERTIFICATE,
        secret_ref=secrets_backend.secret_ref_for(connection_id=uuid.UUID(connection_id), slot="certificate"),
    )
    db_session.add(stray)
    db_session.commit()

    response = client.delete(f"/v1/integrations/connections/{connection_id}/secrets/certificate", headers=headers)

    assert response.status_code == 204, response.text
    assert backend.calls == [("delete", "certificate")]
    assert _refs(db_session, connection_id) == []
