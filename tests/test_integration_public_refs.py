"""KAN-123: `app.integration.public` hands another context plain values, not
the registry's rows. `get_enabled_connection` answers a `ConnectionRef`,
`get_entitlement` an `EntitlementState`, and `call_capability` /
`resolve_adapter` take the reference back and work on the row behind it — only
when that row belongs to the Betrieb the reference names.
"""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from app.integration import public
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import resilience


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

    assert ref == public.ConnectionRef(id=connection.id, tenant_id=tenant_id)
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

    gone = public.ConnectionRef(id=uuid.uuid4(), tenant_id=tenant_id)
    with pytest.raises(public.ConnectionDisabledError):
        public.resolve_adapter(db_session, gone)
    with pytest.raises(public.ConnectionDisabledError), public.call_capability(
        db_session, connection=gone, capability="vehicle_data"
    ):
        pass
    assert db_session.query(IntegrationCallLog).count() == 0


def test_a_reference_naming_another_betrieb_is_refused_like_a_deleted_one(db_session):
    """A reference rebuilt from a stored connection id for the caller's own
    Betrieb must not reach another Betrieb's provider account: no call, no
    call-log row against that Betrieb, no breaker charge, and the same refusal a
    deleted connection gets, so it does not say the connection exists (rule 7)."""

    other_betrieb = uuid.uuid4()
    foreign = _connection(db_session, _provider(db_session), tenant_id=other_betrieb)
    forged = public.ConnectionRef(id=foreign.id, tenant_id=uuid.uuid4())

    with pytest.raises(public.ConnectionDisabledError) as refused:
        public.resolve_adapter(db_session, forged)
    with pytest.raises(public.ConnectionDisabledError), public.call_capability(
        db_session, connection=forged, capability="vehicle_data"
    ) as adapter:
        adapter.fetch_variant("FZ100001")

    deleted = public.ConnectionRef(id=uuid.uuid4(), tenant_id=other_betrieb)
    with pytest.raises(public.ConnectionDisabledError) as gone:
        public.resolve_adapter(db_session, deleted)
    assert str(refused.value) == str(public.ConnectionDisabledError(foreign.id))
    assert type(refused.value) is type(gone.value)
    assert db_session.query(IntegrationCallLog).count() == 0
    assert resilience._CIRCUITS.get(foreign.id) is None  # the breaker was never consulted, nor charged

    own = public.get_enabled_connection(db_session, tenant_id=other_betrieb, provider_code="auto_i_dat_mock")
    assert own == public.ConnectionRef(id=foreign.id, tenant_id=other_betrieb)
    assert isinstance(public.resolve_adapter(db_session, own), MockAutoIDatAdapter)


def test_a_reference_to_a_platform_wide_connection_is_refused_like_a_deleted_one(db_session):
    """A platform-wide connection belongs to no Betrieb: `get_enabled_connection`
    never answers one, and a reference built from its id for any Betrieb is
    refused before the provider, with no call-log row."""

    platform = connection_service.create_connection(
        db_session,
        tenant_id=None,
        data=ConnectionCreate(
            provider_id=_provider(db_session).id,
            display_name="auto-i-dat (platform)",
            environment=ConnectionEnvironment.SANDBOX,
            scope=ConnectionScope.PLATFORM,
        ),
        actor_id=uuid.uuid4(),
    )
    assert platform.tenant_id is None
    betrieb = uuid.uuid4()
    assert public.get_enabled_connection(db_session, tenant_id=betrieb, provider_code="auto_i_dat_mock") is None
    forged = public.ConnectionRef(id=platform.id, tenant_id=betrieb)

    with pytest.raises(public.ConnectionDisabledError):
        public.resolve_adapter(db_session, forged)
    with pytest.raises(public.ConnectionDisabledError), public.call_capability(
        db_session, connection=forged, capability="vehicle_data"
    ):
        pass
    assert db_session.query(IntegrationCallLog).count() == 0
