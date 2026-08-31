"""WP-6 PR-4: the daily-job composition root — enumerates every tenant
with an enabled vehicle-data connection and runs each one's delta+alarm
independently, so one tenant's failure never blocks another's.
"""

import uuid

from app.integration import daily_jobs
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.services import catalogue_sync


def _make_mock_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["vehicle_data", "images", "packages", "valuation", "forecast"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id):
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


def test_run_daily_catalogue_sync_and_alarm_syncs_every_enabled_tenant(db_session):
    provider = _make_mock_provider(db_session)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_a)
    _make_connection(db_session, provider, tenant_id=tenant_b)

    daily_jobs.run_daily_catalogue_sync_and_alarm(db_session)

    for tenant_id in (tenant_a, tenant_b):
        state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
        assert state is not None
        assert state.last_full_seed_at is not None


def test_run_daily_catalogue_sync_and_alarm_does_nothing_for_tenants_with_no_connection(db_session):
    # No providers, no connections at all — must not raise.
    daily_jobs.run_daily_catalogue_sync_and_alarm(db_session)


def test_one_tenants_failure_does_not_block_another_tenants_sync(db_session, monkeypatch):
    provider = _make_mock_provider(db_session)
    broken_tenant, healthy_tenant = uuid.uuid4(), uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=broken_tenant)
    _make_connection(db_session, provider, tenant_id=healthy_tenant)

    real_run_daily_delta_for_tenant = daily_jobs.run_daily_delta_for_tenant

    def _flaky_run_daily_delta_for_tenant(db, *, tenant_id):
        if tenant_id == broken_tenant:
            raise RuntimeError("simulated provider outage")
        return real_run_daily_delta_for_tenant(db, tenant_id=tenant_id)

    monkeypatch.setattr(daily_jobs, "run_daily_delta_for_tenant", _flaky_run_daily_delta_for_tenant)

    daily_jobs.run_daily_catalogue_sync_and_alarm(db_session)

    healthy_state = catalogue_sync.get_sync_state(db_session, tenant_id=healthy_tenant, provider_code="auto_i_dat_mock")
    assert healthy_state is not None
    assert healthy_state.last_full_seed_at is not None

    broken_state = catalogue_sync.get_sync_state(db_session, tenant_id=broken_tenant, provider_code="auto_i_dat_mock")
    assert broken_state is None  # never got as far as writing sync state
