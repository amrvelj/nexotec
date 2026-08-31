"""WP-6 PR-3: the real auto-i-dat adapter — AES decrypt, in-memory-only
credential resolution audited with actor/tenant/connection/purpose,
timeout+one-retry-with-jitter, and a per-connection circuit breaker.

No live WSDL or real vendor sample exists in this environment (flagged in
auto_i_dat_soap.py's own module docstring as provisional) — `FakeSoapClient`
below stands in for a real `zeep` service proxy, matching `SoapClient`'s
structural shape rather than a real SOAP transport.
"""

import time
import uuid

import pytest

from app.core.audit import list_audit_events
from app.integration.adapters import auto_i_dat_soap
from app.integration.adapters.aes_decrypt import decrypt_aes_cbc, encrypt_aes_cbc
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import resilience


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


def _make_connection(db_session, provider, *, tenant_id=None):
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


class _Response:
    """A stand-in for a zeep response object — plain attribute access,
    same as a real SOAP response's generated object would offer.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeSoapClient:
    """Matches `SoapClient`'s structural shape. `fail_times` lets a test
    make the first N calls to any operation raise, to exercise the
    timeout-with-one-retry path without a real network flake.
    """

    def __init__(self, *, fail_times: int = 0):
        self.calls: list[tuple[str, dict]] = []
        self._fail_times = fail_times

    def _maybe_fail(self, operation: str, kwargs: dict) -> None:
        self.calls.append((operation, kwargs))
        if len(self.calls) <= self._fail_times:
            raise ConnectionError("simulated transient SOAP failure")

    def Fahrzeuge(self, **kwargs):
        self._maybe_fail("Fahrzeuge", kwargs)
        return _Response(MarkeCode="ALF", Marke="Alfa Romeo", ModellGruppe="Giulietta", Typ="1.4 TB", BaujahrVon=2019)

    def FzKeyChanged(self, **kwargs):
        self._maybe_fail("FzKeyChanged", kwargs)
        return _Response(FzKeys=["FZ100001"])

    def System(self, **kwargs):
        self._maybe_fail("System", kwargs)
        return _Response(AktuellesModelljahr=2026, StandDatum="2026-08-30")

    def Optionen(self, **kwargs):
        self._maybe_fail("Optionen", kwargs)
        return _Response(Optionen=[])

    def OptionenFarben(self, **kwargs):
        self._maybe_fail("OptionenFarben", kwargs)
        return _Response(Farben=[])

    def PneuDimTS(self, **kwargs):
        self._maybe_fail("PneuDimTS", kwargs)
        return _Response(Pneus=[])

    def Bilder(self, **kwargs):
        self._maybe_fail("Bilder", kwargs)
        return _Response(Bilder=[])


class FakeSecretsBackend:
    """In-memory stand-in for services/secrets_backend.py, matching the
    pattern already established in tests/test_integration_registry.py's
    own FakeSecretsBackend."""

    def __init__(self, values: dict[tuple[uuid.UUID, str], str]):
        self._values = values

    def resolve_secret(self, *, connection_id: uuid.UUID, slot: str) -> str:
        return self._values[(connection_id, slot)]


# --- AES decrypt --------------------------------------------------------------


def test_aes_roundtrip_against_a_self_constructed_vector():
    key = b"0123456789abcdef"  # 16 bytes -> AES-128
    iv = b"fedcba9876543210"
    plaintext = b"a real vendor sample would go here"

    ciphertext = encrypt_aes_cbc(plaintext, key=key, iv=iv)
    assert decrypt_aes_cbc(ciphertext, key=key) == plaintext


def test_aes_decrypt_rejects_a_wrong_length_key():
    with pytest.raises(ValueError, match="16, 24 or 32 bytes"):
        decrypt_aes_cbc(b"x" * 32, key=b"too-short")


def test_aes_decrypt_rejects_ciphertext_too_short_for_an_iv():
    with pytest.raises(ValueError, match="too short"):
        decrypt_aes_cbc(b"short", key=b"0123456789abcdef")


# --- credential resolution: in-memory only, audited -----------------------


def test_credential_resolution_is_audit_logged_with_actor_tenant_connection_purpose(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    actor_id = uuid.uuid4()

    fake_backend = FakeSecretsBackend({(connection.id, "password"): "s3cret", (connection.id, "aes_key"): "0123456789abcdef"})
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", fake_backend)

    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=FakeSoapClient(), actor_id=actor_id, purpose="vehicle_data"
    )
    adapter.fetch_vehicle_master_data("FZ100001")

    events = list_audit_events(db_session, entity_type="integration_secret_ref", entity_id=connection.id, tenant_id=connection.tenant_id)
    assert len(events) == 1
    assert events[0].action == "secret_resolved"
    assert events[0].actor_id == actor_id
    assert events[0].tenant_id == connection.tenant_id
    assert "slot=password" in events[0].reason
    assert "purpose=vehicle_data" in events[0].reason


def test_credential_resolution_never_persists_the_secret_value_anywhere(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    fake_backend = FakeSecretsBackend({(connection.id, "password"): "s3cret-value", (connection.id, "aes_key"): "0123456789abcdef"})
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", fake_backend)

    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=FakeSoapClient(), actor_id=uuid.uuid4(), purpose="vehicle_data"
    )
    adapter.fetch_vehicle_master_data("FZ100001")

    # The only row this resolution wrote is the audit event, and its own
    # reason string never carries the resolved value — only the slot name.
    events = list_audit_events(db_session, entity_type="integration_secret_ref", entity_id=connection.id, tenant_id=connection.tenant_id)
    assert "s3cret-value" not in events[0].reason
    assert not hasattr(adapter, "_password_cache")  # never cached on the instance either


# --- resolve_secret is never exported from app.integration.public --------


def test_resolve_secret_not_exported_from_public():
    from app.integration import public

    assert "resolve_secret" not in public.__all__
    assert not hasattr(public, "resolve_secret")


# --- timeout + one retry with jitter --------------------------------------


def test_soap_call_retries_once_on_transient_failure_then_succeeds(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))  # keep the test fast
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    fake_backend = FakeSecretsBackend({(connection.id, "password"): "s3cret", (connection.id, "aes_key"): "0123456789abcdef"})
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", fake_backend)

    soap_client = FakeSoapClient(fail_times=1)
    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=soap_client, actor_id=uuid.uuid4(), purpose="vehicle_data"
    )

    result = adapter.fetch_vehicle_master_data("FZ100001")
    assert result.brand_display_name == "Alfa Romeo"
    assert len(soap_client.calls) == 2  # one failure, one retry that succeeded


def test_soap_call_gives_up_after_one_retry_and_raises(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    fake_backend = FakeSecretsBackend({(connection.id, "password"): "s3cret", (connection.id, "aes_key"): "0123456789abcdef"})
    monkeypatch.setattr(auto_i_dat_soap, "secrets_backend", fake_backend)

    soap_client = FakeSoapClient(fail_times=2)  # both the first attempt and the one retry fail
    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=soap_client, actor_id=uuid.uuid4(), purpose="vehicle_data"
    )

    with pytest.raises(ConnectionError):
        adapter.fetch_vehicle_master_data("FZ100001")
    assert len(soap_client.calls) == 2  # never more than one retry


# --- circuit breaker: opens after the threshold, per connection ----------


def test_circuit_breaker_opens_after_the_failure_threshold_and_short_circuits():
    connection_id = uuid.uuid4()
    resilience.reset_circuit(connection_id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_id)
        assert resilience.is_circuit_open(connection_id) is True
    finally:
        resilience.reset_circuit(connection_id)


def test_circuit_breaker_state_is_per_connection_not_global():
    connection_a = uuid.uuid4()
    connection_b = uuid.uuid4()
    resilience.reset_circuit(connection_a)
    resilience.reset_circuit(connection_b)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_a)
        assert resilience.is_circuit_open(connection_a) is True
        assert resilience.is_circuit_open(connection_b) is False
    finally:
        resilience.reset_circuit(connection_a)
        resilience.reset_circuit(connection_b)


def test_circuit_breaker_recovers_half_open_after_the_open_duration(monkeypatch):
    connection_id = uuid.uuid4()
    resilience.reset_circuit(connection_id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_id)
        assert resilience.is_circuit_open(connection_id) is True

        # Simulate the open window having elapsed without a real sleep.
        real_monotonic = time.monotonic
        monkeypatch.setattr(resilience.time, "monotonic", lambda: real_monotonic() + resilience._OPEN_DURATION_SECONDS + 1)
        assert resilience.is_circuit_open(connection_id) is False
    finally:
        resilience.reset_circuit(connection_id)


def test_gateway_call_capability_refuses_when_circuit_is_open(db_session):
    from app.integration.services import gateway

    provider = _make_provider(db_session, provider_code="auto_i_dat_mock", auth_type="none", required_secret_slots=[])
    connection = connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    resilience.reset_circuit(connection.id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection.id)

        with pytest.raises(resilience.CircuitOpenError), gateway.call_capability(
            db_session, connection=connection, capability="vehicle_data"
        ):
            pass
    finally:
        resilience.reset_circuit(connection.id)
