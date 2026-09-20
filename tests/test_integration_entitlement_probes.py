"""Configurator C-0 / KAN-38 PR 2b: entitlement probing.

`gateway.test_connection` probes `fahrzeuge` after `System` succeeds and
records the result as an `integration_entitlement` row. The protocol has no
"not entitled" signal (Webservice Fahrzeuge p4), so what is tested here is
the *inference* and the discipline around it — never that the inference is
right about a real account, which needs the staging account.
"""

import uuid

import pytest

from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.auto_i_dat_parse import ProviderMaintenanceError, ProviderRejectedError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus
from app.integration.models.entitlement import EntitlementSource
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import entitlement_probes, gateway, resilience
from app.integration.services.gateway import ProviderGatewayError

_MOCK_PROVIDER_CODE = "auto_i_dat_mock"


def _make_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": _MOCK_PROVIDER_CODE,
        "category": "vehicle_data",
        "display_name": "auto-i-dat (mock)",
        "auth_type": "none",
        "required_secret_slots": [],
        "capability_codes": ["fahrzeuge"],
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider):
    return connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )


class _RefusingAdapter(MockAutoIDatAdapter):
    """`System` answers (so the credentials are proven good), `FahrzeugArten`
    comes back as the empty string the real adapter turns into a rejection.
    """

    def list_vehicle_kinds(self):
        raise ProviderRejectedError("empty response")


class _OutageAdapter(MockAutoIDatAdapter):
    def list_vehicle_kinds(self):
        raise ProviderMaintenanceError("Webservice ist offline (Wartung)")


class _WatermarkFailsAdapter(MockAutoIDatAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.probed = False

    def get_system_watermark(self):
        raise ProviderGatewayError("System refused the credentials")

    def list_vehicle_kinds(self):
        self.probed = True
        return super().list_vehicle_kinds()


def _serve(monkeypatch, adapter, provider_code: str = _MOCK_PROVIDER_CODE) -> None:
    monkeypatch.setitem(gateway._ADAPTER_FACTORIES, provider_code, lambda db, connection, actor_id, purpose: adapter)


def _fahrzeuge(db_session, connection):
    return connection_service.get_entitlement(db_session, connection_id=connection.id, capability_code="fahrzeuge")


def _calls(db_session, connection, capability):
    return (
        db_session.query(IntegrationCallLog)
        .filter_by(connection_id=connection.id, capability=capability)
        .order_by(IntegrationCallLog.created_at)
        .all()
    )


def test_run_probe_reads_only_a_rejection_as_not_granted():
    probe = entitlement_probes.probes_for(_MOCK_PROVIDER_CODE)[0]

    assert entitlement_probes.run_probe(probe, MockAutoIDatAdapter()) is True
    assert entitlement_probes.run_probe(probe, _RefusingAdapter()) is False
    # An outage says nothing about entitlement — it must not be swallowed
    # into either answer.
    with pytest.raises(ProviderMaintenanceError):
        entitlement_probes.run_probe(probe, _OutageAdapter())


# --- through gateway.test_connection ----------------------------------------------


def test_test_connection_records_fahrzeuge_granted_for_an_entitled_account(db_session):
    connection = _make_connection(db_session, _make_provider(db_session))
    assert _fahrzeuge(db_session, connection) is None

    updated = gateway.test_connection(db_session, connection=connection)

    assert updated.status == ConnectionStatus.CONNECTED
    row = _fahrzeuge(db_session, connection)
    assert row is not None
    assert row.granted is True
    assert row.source == EntitlementSource.PROBED
    assert row.checked_at is not None


def test_a_refusal_records_not_granted_but_the_connection_itself_stays_healthy(db_session, monkeypatch):
    connection = _make_connection(db_session, _make_provider(db_session))
    _serve(monkeypatch, _RefusingAdapter())

    updated = gateway.test_connection(db_session, connection=connection)

    row = _fahrzeuge(db_session, connection)
    assert row is not None
    assert row.granted is False
    # `System` answered, so the connection is fine; one un-entitled
    # capability must not read as a broken connection ...
    assert updated.status == ConnectionStatus.CONNECTED
    assert updated.last_error is None
    # ... nor switch off any other capability.
    assert connection_service.get_entitlement(db_session, connection_id=connection.id, capability_code="optionen") is None


def test_a_refusal_is_never_charged_to_the_connections_circuit_breaker(db_session, monkeypatch):
    """The breaker is per *connection*, so a refusal that counted as a
    failure would eventually take `System` and every other capability
    offline. Inside `test_connection` alone it could never get that far —
    each click's successful `System` call resets the counter — which is why
    this spies on `record_failure` instead of asserting the circuit stayed
    closed: that assertion passes even when the refusal *is* charged. What
    it protects is a probe run outside that reset (the opportunistic
    probing planned for C-D).
    """

    connection = _make_connection(db_session, _make_provider(db_session))
    _serve(monkeypatch, _RefusingAdapter())
    charged: list[uuid.UUID] = []
    monkeypatch.setattr(resilience, "record_failure", charged.append)

    for _ in range(6):
        gateway.test_connection(db_session, connection=connection)

    assert charged == []
    refusals = _calls(db_session, connection, "fahrzeuge")
    assert len(refusals) == 6
    # The provider answered coherently, so the gateway logs it as a success.
    assert {call.status for call in refusals} == {CallStatus.SUCCESS}


def test_a_later_grant_flips_the_same_row_rather_than_adding_one(db_session, monkeypatch):
    connection = _make_connection(db_session, _make_provider(db_session))

    _serve(monkeypatch, _RefusingAdapter())
    gateway.test_connection(db_session, connection=connection)
    refused = _fahrzeuge(db_session, connection)
    assert refused is not None and refused.granted is False
    first_checked_at = refused.checked_at

    _serve(monkeypatch, MockAutoIDatAdapter())
    gateway.test_connection(db_session, connection=connection)

    rows = [
        e for e in connection_service.list_entitlements(db_session, connection_id=connection.id)
        if e.capability_code == "fahrzeuge"
    ]
    assert len(rows) == 1
    assert rows[0].granted is True
    assert rows[0].checked_at >= first_checked_at


def test_a_transient_probe_failure_leaves_the_entitlement_exactly_as_it_was(db_session, monkeypatch):
    connection = _make_connection(db_session, _make_provider(db_session))
    _serve(monkeypatch, _OutageAdapter())

    # Never probed: an outage must not invent a row...
    gateway.test_connection(db_session, connection=connection)
    assert _fahrzeuge(db_session, connection) is None

    # ...and an existing verdict must not be flipped by one either.
    connection_service.record_probed_entitlement(
        db_session, connection_id=connection.id, capability_code="fahrzeuge", granted=False
    )
    before = _fahrzeuge(db_session, connection)
    assert before is not None
    checked_at = before.checked_at

    updated = gateway.test_connection(db_session, connection=connection)

    after = _fahrzeuge(db_session, connection)
    assert after is not None
    assert (after.granted, after.checked_at) == (False, checked_at)
    # The probe failing never fails "Test connection" — `System` was fine.
    assert updated.status == ConnectionStatus.CONNECTED
    # And an outage is honestly recorded as one, unlike a refusal.
    assert {call.status for call in _calls(db_session, connection, "fahrzeuge")} == {CallStatus.ERROR}


def test_a_connection_that_fails_the_watermark_check_is_never_probed(db_session, monkeypatch):
    """The differential only means anything if `System` was accepted first —
    otherwise an empty string could equally be a bad password.
    """

    connection = _make_connection(db_session, _make_provider(db_session))
    adapter = _WatermarkFailsAdapter()
    _serve(monkeypatch, adapter)

    updated = gateway.test_connection(db_session, connection=connection)

    assert updated.status == ConnectionStatus.ERROR
    assert adapter.probed is False
    assert _calls(db_session, connection, "fahrzeuge") == []
    assert _fahrzeuge(db_session, connection) is None


def test_a_provider_with_no_registered_probes_gets_no_entitlement_rows(db_session, monkeypatch):
    provider = _make_provider(db_session, provider_code="some_other_provider")
    connection = _make_connection(db_session, provider)
    _serve(monkeypatch, MockAutoIDatAdapter(), provider_code="some_other_provider")

    updated = gateway.test_connection(db_session, connection=connection)

    assert updated.status == ConnectionStatus.CONNECTED
    assert connection_service.list_entitlements(db_session, connection_id=connection.id) == []
    assert _calls(db_session, connection, "fahrzeuge") == []
