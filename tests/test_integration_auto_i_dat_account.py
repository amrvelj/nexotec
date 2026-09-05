"""KAN-36 — the real auto-i-dat account (BenutzerNr + BenutzerInfo) and the
DAT sub-account that entitles VIN decode. All fixture values below are
invented, with the right shapes (small integer BenutzerNr, 7-digit DAT
account number) — never a value from the real account sheet.
"""

import uuid

import pytest

from app.core.errors import UnprocessableEntityError
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus
from app.integration.models.entitlement import EntitlementSource
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import SecretSlot
from app.integration.schemas.connection import ConnectionCreate, ConnectionUpdate, SecretSlotRead
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
                config={"username": "efdealer"},  # benutzerNr/benutzerInfo missing
            ),
            actor_id=uuid.uuid4(),
        )
    assert "benutzerNr" in str(excinfo.value.details["missingConfigKeys"])


def test_auto_i_dat_connection_with_full_account_shape_succeeds(db_session):
    provider = _make_auto_i_dat_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX,
            config={"username": "efdealer", "benutzerNr": 4711, "benutzerInfo": "DCMi"},
        ),
        actor_id=uuid.uuid4(),
    )
    assert connection.config["benutzerNr"] == 4711
    assert connection.config["benutzerInfo"] == "DCMi"


def test_update_dropping_a_required_config_key_is_refused(db_session):
    provider = _make_auto_i_dat_provider(db_session)
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="Test", environment=ConnectionEnvironment.SANDBOX,
            config={"username": "efdealer", "benutzerNr": 4711, "benutzerInfo": "DCMi"},
        ),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(UnprocessableEntityError):
        connection_service.update_connection(
            db_session, connection=connection,
            data=ConnectionUpdate(config={"username": "efdealer"}),  # drops benutzerNr/benutzerInfo
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
            config={"accountNumber": "1234567", "username": "efdealer"},
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
                config={"username": "efdealer"},  # accountNumber missing
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
            config={"accountNumber": "1234567", "username": "efdealer"},
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
            config={"accountNumber": "1234567", "username": "efdealer"},
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
            config={"accountNumber": "1234567", "username": "efdealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    auto_i_dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=auto_i_dat_provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "efdealer", "benutzerNr": 4711, "benutzerInfo": "DCMi"},
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

    entitlement = connection_service.get_entitlement(
        db_session, connection_id=auto_i_dat_connection.id, capability_code="vin_decode"
    )
    assert entitlement is not None
    assert entitlement.granted is True
    assert entitlement.source == EntitlementSource.PROBED


def test_removing_the_dat_connection_revokes_vin_decode(db_session):
    dat_provider = _make_dat_provider(db_session)
    auto_i_dat_provider = _make_auto_i_dat_provider(db_session)
    tenant_id = uuid.uuid4()

    dat_connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=dat_provider.id, display_name="DAT", environment=ConnectionEnvironment.PRODUCTION,
            config={"accountNumber": "1234567", "username": "efdealer"},
        ),
        actor_id=uuid.uuid4(),
    )
    connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=auto_i_dat_provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.PRODUCTION,
            config={"username": "efdealer", "benutzerNr": 4711, "benutzerInfo": "DCMi"},
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
