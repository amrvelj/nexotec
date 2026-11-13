"""KAN-123: `app.integration.public` hands another context plain values, not
the registry's rows. `get_enabled_connection` answers a `ConnectionRef`,
`get_entitlement` an `EntitlementState`, and `call_capability` /
`resolve_adapter` take the reference back and work on the row behind it.
"""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from app.integration import public
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service


def _provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["ident_basis", "ausstattung"],
    )
    db_session.add(provider)
    db_session.commit()
    return provider


def _connection(db_session, provider, *, tenant_id, enabled=True) -> IntegrationConnection:
    connection = connection_service.create_connection(
        db_session,
        tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    if not enabled:
        connection = connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    return connection


def test_get_enabled_connection_answers_a_frozen_reference_not_the_row(db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, _provider(db_session), tenant_id=tenant_id)

    ref = public.get_enabled_connection(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")

    assert ref == public.ConnectionRef(id=connection.id)
    assert not isinstance(ref, IntegrationConnection)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.id = uuid.uuid4()  # type: ignore[misc]


def test_get_enabled_connection_answers_none_without_an_enabled_one(db_session):
    provider = _provider(db_session)
    disabled_tenant = uuid.uuid4()
    _connection(db_session, provider, tenant_id=disabled_tenant, enabled=False)

    assert public.get_enabled_connection(db_session, tenant_id=disabled_tenant, provider_code="auto_i_dat_mock") is None
    assert public.get_enabled_connection(db_session, tenant_id=uuid.uuid4(), provider_code="auto_i_dat_mock") is None


def test_get_entitlement_answers_the_state_or_none_when_never_probed(db_session):
    connection = _connection(db_session, _provider(db_session), tenant_id=uuid.uuid4())
    row = connection_service.record_probed_entitlement(
        db_session, connection_id=connection.id, capability_code="ausstattung", granted=False
    )
    db_session.commit()

    state = public.get_entitlement(db_session, connection_id=connection.id, capability_code="ausstattung")

    assert state == public.EntitlementState(granted=False, checked_at=row.checked_at)
    assert public.get_entitlement(db_session, connection_id=connection.id, capability_code="ident_basis") is None


def test_call_capability_takes_the_reference_back_and_logs_the_request(db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, _provider(db_session), tenant_id=tenant_id)
    ref = public.get_enabled_connection(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert ref is not None

    with public.call_capability(db_session, connection=ref, capability="vehicle_data") as adapter:
        assert adapter.fetch_variant("FZ100001") is not None

    logs = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert [(log.status, log.capability, log.cost_units) for log in logs] == [
        (CallStatus.SUCCESS, "vehiclecatalog", Decimal(1))
    ]


def test_resolve_adapter_takes_the_reference_back(db_session):
    tenant_id = uuid.uuid4()
    _connection(db_session, _provider(db_session), tenant_id=tenant_id)
    ref = public.get_enabled_connection(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert ref is not None

    assert isinstance(public.resolve_adapter(db_session, ref), MockAutoIDatAdapter)


def test_a_reference_to_a_disabled_or_deleted_connection_is_refused_before_the_provider(db_session):
    tenant_id = uuid.uuid4()
    connection = _connection(db_session, _provider(db_session), tenant_id=tenant_id)
    ref = public.get_enabled_connection(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert ref is not None
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    with pytest.raises(public.ConnectionDisabledError), public.call_capability(
        db_session, connection=ref, capability="vehicle_data"
    ):
        pass

    gone = public.ConnectionRef(id=uuid.uuid4())
    with pytest.raises(public.ConnectionDisabledError):
        public.resolve_adapter(db_session, gone)
    with pytest.raises(public.ConnectionDisabledError), public.call_capability(
        db_session, connection=gone, capability="vehicle_data"
    ):
        pass
    assert db_session.query(IntegrationCallLog).count() == 0
