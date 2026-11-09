"""KAN-36 — the auto-i-dat account's required config, and `vin_decode`,
which since KAN-549 follows the identAPI's licence option `vin` strictly
(the DAT sub-account model is retired, ADR-113). All fixture values below
are invented and read as such (KAN-491: «Testprojekt A», BenutzerNr 9001,
username test-dealer) — never a value from a real account sheet.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate, ConnectionUpdate
from app.integration.services import connections as connection_service


class FakeSecretsBackend:
    """Same in-memory stand-in as tests/test_integration_registry.py."""

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


def _make_auto_i_dat_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "auto_i_dat",
        "category": "vehicle_data",
        "display_name": "auto-i-dat",
        "auth_type": "soap_password_aes",
        "required_secret_slots": ["password", "aes_key"],
        "required_config_keys": ["username", "benutzerNr", "benutzerInfo"],
        "capability_codes": ["fahrzeuge", "optionen", "kontrollschild", "pneu", "bewertung", "vin", "vin_ident_db", "ins_tc"],
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


# --- required config keys ---------------------------------------------------


def test_auto_i_dat_connection_without_benutzer_nr_is_refused(db_session):
    provider = _make_auto_i_dat_provider(db_session)
    with pytest.raises(UnprocessableEntityError) as excinfo:
        connection_service.create_connection(
            db_session, tenant_id=uuid.uuid4(),
            data=ConnectionCreate(
                provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX,
                config={"username": "test-dealer"},  # benutzerNr/benutzerInfo missing
            ),
            actor_id=uuid.uuid4(),
        )
    assert "benutzerNr" in str(excinfo.value.details["missingConfigKeys"])
    assert excinfo.value.details["reason"] == "missing_config_fields"


def test_auto_i_dat_connection_without_benutzer_nr_via_api_names_its_reason(client, db_session):
    """KAN-472: through the route, the 422 carries a stable `details.reason`
    beside the missing keys."""

    from tests.test_integration_idempotency import _headers, _manager_token

    provider = _make_auto_i_dat_provider(db_session)
    response = client.post(
        "/v1/integrations/connections",
        json={
            "providerId": str(provider.id),
            "displayName": "Test",
            "environment": "sandbox",
            "config": {"username": "test-dealer"},
        },
        headers=_headers(_manager_token()),
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "missing_config_fields"
    assert "benutzerNr" in details["missingConfigKeys"]


def test_auto_i_dat_connection_with_full_account_shape_succeeds(db_session):
    provider = _make_auto_i_dat_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )
    assert connection.config["benutzerNr"] == 9001
    assert connection.config["benutzerInfo"] == "Testprojekt A"


def test_update_dropping_a_required_config_key_is_refused(db_session):
    provider = _make_auto_i_dat_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(UnprocessableEntityError):
        connection_service.update_connection(
            db_session, connection=connection,
            data=ConnectionUpdate(config={"username": "test-dealer"}),  # drops benutzerNr/benutzerInfo
            actor_id=uuid.uuid4(),
        )


# --- vin_decode: the licence option `vin`, read strictly (KAN-549) ---------


def _auto_i_dat_connection(db_session, tenant_id):
    provider = _make_auto_i_dat_provider(db_session)
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )


def _vin_decode(db_session, tenant_id) -> bool:
    return connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode")


def test_vin_decode_absent_without_a_connection(db_session):
    assert _vin_decode(db_session, uuid.uuid4()) is False


def test_vin_decode_absent_on_a_connection_never_probed(db_session):
    """Unlike every other option, no row is NOT granted: the test licence does
    not grant `vin`, and the rung must not learn that from a billed refusal."""

    tenant_id = uuid.uuid4()
    connection = _auto_i_dat_connection(db_session, tenant_id)
    assert _vin_decode(db_session, tenant_id) is False
    assert connection_service.vin_decode_granted(db_session, connection_id=connection.id) is False


@pytest.mark.parametrize("granted", [True, False])
def test_vin_decode_follows_the_probed_vin_row_and_nothing_else(db_session, granted):
    tenant_id = uuid.uuid4()
    connection = _auto_i_dat_connection(db_session, tenant_id)
    connection_service.record_probed_entitlement(
        db_session, connection_id=connection.id, capability_code="vin", granted=granted
    )
    assert _vin_decode(db_session, tenant_id) is granted
    assert connection_service.vin_decode_granted(db_session, connection_id=connection.id) is granted


def test_the_capability_get_never_writes_an_entitlement(client, db_session):
    """KAN-80 (Anto, option A): `GET /v1/integrations/capabilities/vin_decode`
    is a read any user of the tenant may send; it writes no row."""

    tenant_id = uuid.uuid4()
    connection = _auto_i_dat_connection(db_session, tenant_id)
    connection_service.record_probed_entitlement(db_session, connection_id=connection.id, capability_code="vin", granted=True)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )

    for _ in range(2):
        resp = client.get("/v1/integrations/capabilities/vin_decode", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"capabilityCode": "vin_decode", "granted": True}

    db_session.expire_all()
    assert db_session.query(IntegrationEntitlement).count() == 1


def test_disabling_or_removing_the_connection_revokes_vin_decode(db_session):
    tenant_id = uuid.uuid4()
    connection = _auto_i_dat_connection(db_session, tenant_id)
    connection_service.record_probed_entitlement(db_session, connection_id=connection.id, capability_code="vin", granted=True)
    assert _vin_decode(db_session, tenant_id) is True

    connection.enabled = False
    db_session.commit()
    assert _vin_decode(db_session, tenant_id) is False

    connection.enabled = True
    db_session.commit()
    connection_service.delete_connection(
        db_session, connection=connection, confirm=True, actor_id=uuid.uuid4(),
        secrets_backend_module=FakeSecretsBackend(),
    )
    assert _vin_decode(db_session, tenant_id) is False
