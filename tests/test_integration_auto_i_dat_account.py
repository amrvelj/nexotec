"""KAN-36 — the real auto-i-dat account (BenutzerNr + BenutzerInfo) and the
DAT sub-account that entitles VIN decode. All fixture values below are
invented and read as such (KAN-491: «Testprojekt A», BenutzerNr 9001, DAT
account 9900001, username test-dealer), with the right shapes (small
integer BenutzerNr, 7-digit DAT account number) — never a value from a
real account sheet.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import SecretSlot
from app.integration.schemas.connection import ConnectionCreate, ConnectionUpdate, SecretSlotRead
from app.integration.services import connections as connection_service
from app.integration.services import gateway


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


def _make_dat_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "dat",
        "category": "vehicle_data",
        "display_name": "DAT",
        "auth_type": "username_password",
        "required_secret_slots": ["password"],
        "required_config_keys": ["accountNumber", "username"],
        "capability_codes": ["vin", "vin_ident_db"],
        "supports_sandbox": False,
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


# --- the dat sub-account: its own connection, reusing existing shapes -------


def test_dat_connection_can_be_created_tested_and_rotated(db_session):
    provider = _make_dat_provider(db_session)
    backend = FakeSecretsBackend()
    tenant_id = uuid.uuid4()

    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    ref = connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="s3cr3t",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    assert not hasattr(ref, "value")
    assert ref.secret_ref != "s3cr3t"

    rotated = connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="new-secret",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    assert rotated.secret_ref != "new-secret"
    assert backend.store[f"{connection.id}:password"] == "new-secret"


def test_dat_connection_without_accountnumber_is_refused(db_session):
    provider = _make_dat_provider(db_session)
    with pytest.raises(UnprocessableEntityError):
        connection_service.create_connection(
            db_session, tenant_id=uuid.uuid4(),
            data=ConnectionCreate(
                provider_id=provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
                config={"username": "test-dealer"},  # accountNumber missing
            ),
            actor_id=uuid.uuid4(),
        )


def test_no_dat_secret_is_ever_returned(db_session, client):
    provider = _make_dat_provider(db_session)
    backend = FakeSecretsBackend()
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    ref = connection_service.set_secret(
        db_session, connection=connection, slot=SecretSlot.PASSWORD, value="s3cr3t",
        actor_id=uuid.uuid4(), secrets_backend_module=backend,
    )
    assert not hasattr(ref, "value")
    assert not hasattr(ref, "secret_value")
    assert ref.secret_ref != "s3cr3t"  # a pointer, never the value

    slots = connection_service.list_secret_slots(db_session, connection_id=connection.id)
    for slot in slots:
        serialized = SecretSlotRead.model_validate(slot, from_attributes=True).model_dump()
        assert "s3cr3t" not in serialized.values()
        assert "secretRef" not in serialized  # the API-facing shape never carries the pointer either


# --- vin_decode: derived, never hand-declared -------------------------------


def test_vin_decode_absent_with_no_dat_connection(db_session):
    tenant_id = uuid.uuid4()
    assert connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode") is False


def test_vin_decode_absent_with_an_unhealthy_dat_connection(db_session):
    provider = _make_dat_provider(db_session)
    tenant_id = uuid.uuid4()
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    # freshly created connections default to NOT_CONFIGURED, never CONNECTED
    assert connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode") is False


def test_vin_decode_granted_with_a_healthy_dat_connection(db_session):
    dat_provider = _make_dat_provider(db_session)
    auto_i_dat_provider = _make_auto_i_dat_provider(db_session)
    tenant_id = uuid.uuid4()

    dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=dat_provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    auto_i_dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=auto_i_dat_provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )

    # No live DAT adapter exists yet (no webservice spec) — a real "Test
    # connection" call can never reach CONNECTED. Setting status directly
    # is how this test proves the *derivation* is correct in isolation
    # from that (currently impossible) live probe.
    dat_connection.status = ConnectionStatus.CONNECTED
    db_session.commit()

    granted = connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode")
    assert granted is True
    # KAN-80: the answer is derived, and asking writes nothing.
    assert connection_service.get_entitlement(
        db_session, connection_id=auto_i_dat_connection.id, capability_code="vin_decode"
    ) is None


def _dat_and_auto_i_dat(db_session, tenant_id):
    dat_provider = _make_dat_provider(db_session)
    auto_i_dat_provider = _make_auto_i_dat_provider(db_session)
    dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=dat_provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    auto_i_dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=auto_i_dat_provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )
    return dat_connection, auto_i_dat_connection


def test_the_capability_get_never_writes_the_vin_decode_entitlement(client, db_session):
    """KAN-80 (Anto, option A): `GET /v1/integrations/capabilities/vin_decode`
    is a read any user of the tenant may send. Before, every call upserted
    the cached `integration_entitlement` row on the auto-i-dat connection."""

    tenant_id = uuid.uuid4()
    dat_connection, _auto_i_dat_connection = _dat_and_auto_i_dat(db_session, tenant_id)
    dat_connection.status = ConnectionStatus.CONNECTED
    db_session.commit()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )

    for _ in range(2):
        resp = client.get("/v1/integrations/capabilities/vin_decode", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"capabilityCode": "vin_decode", "granted": True}

    db_session.expire_all()
    assert db_session.query(IntegrationEntitlement).count() == 0


def test_testing_the_dat_connection_refreshes_the_cached_vin_decode_entitlement(db_session, monkeypatch):
    """KAN-80: the cached row is written where the entitlement is established
    — an admin's "Test connection" on the `dat` (or `auto_i_dat`) connection.
    No DAT adapter exists yet (KAN-81), so the test serves the mock."""

    tenant_id = uuid.uuid4()
    dat_connection, auto_i_dat_connection = _dat_and_auto_i_dat(db_session, tenant_id)
    monkeypatch.setitem(gateway._ADAPTER_FACTORIES, "dat", lambda db, connection, actor_id, purpose: MockAutoIDatAdapter())

    tested = gateway.test_connection(db_session, connection=dat_connection)

    assert tested.status == ConnectionStatus.CONNECTED
    entitlement = connection_service.get_entitlement(
        db_session, connection_id=auto_i_dat_connection.id, capability_code="vin_decode"
    )
    assert entitlement is not None
    assert entitlement.granted is True
    assert entitlement.source == EntitlementSource.PROBED

    # A failed test refreshes it too: the DAT account is no longer healthy.
    monkeypatch.delitem(gateway._ADAPTER_FACTORIES, "dat")
    tested = gateway.test_connection(db_session, connection=dat_connection)

    assert tested.status == ConnectionStatus.ERROR
    db_session.refresh(entitlement)
    assert entitlement.granted is False


def test_removing_the_dat_connection_revokes_vin_decode(db_session):
    dat_provider = _make_dat_provider(db_session)
    auto_i_dat_provider = _make_auto_i_dat_provider(db_session)
    tenant_id = uuid.uuid4()

    dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=dat_provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "9900001", "username": "test-dealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=auto_i_dat_provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "test-dealer", "benutzerNr": 9001, "benutzerInfo": "Testprojekt A"},
        ),
        actor_id=uuid.uuid4(),
    )
    dat_connection.status = ConnectionStatus.CONNECTED
    db_session.commit()
    assert connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode") is True

    connection_service.delete_connection(
        db_session, connection=dat_connection, confirm=True, actor_id=uuid.uuid4(),
        secrets_backend_module=FakeSecretsBackend(),
    )

    assert connection_service.tenant_has_capability(db_session, tenant_id=tenant_id, capability_code="vin_decode") is False
