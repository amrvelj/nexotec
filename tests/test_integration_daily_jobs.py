"""WP-6 PR-4: the daily-job composition root — enumerates every tenant
with an enabled vehicle-data connection and runs each one's delta+alarm
independently, so one tenant's failure never blocks another's.
"""

import uuid

from app.integration import daily_jobs
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.notification import IntegrationNotification, NotificationKind
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


# --- WP-6 PR-6: the combined composition root ------------------------------


def test_run_daily_integration_jobs_ties_sync_alarm_purge_and_digest_together(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    daily_jobs.run_daily_integration_jobs(db_session)

    # Sync ran (PR-4's own effect, confirmed already above) — here the
    # NEW assertion is that retention purge and notification steps also
    # ran without raising, in the same call.
    state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert state is not None


def test_run_daily_retention_purge_reports_zero_on_a_clean_database(db_session):
    counts = daily_jobs.run_daily_retention_purge(db_session)
    assert counts == {"callLogMetadata": 0, "errorPayloads": 0, "successPayloads": 0}


def test_run_daily_integration_jobs_sends_a_digest_when_an_alarm_fires(db_session, monkeypatch):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    # Force the alarm to fire regardless of the mock adapter's own
    # (healthy, today-dated) watermark, isolating "does the digest step
    # receive and act on what sync+alarm found" from sync's own timing.
    monkeypatch.setattr(daily_jobs, "run_daily_catalogue_sync_and_alarm", lambda db: [tenant_id])

    daily_jobs.run_daily_integration_jobs(db_session)

    digest_rows = db_session.query(IntegrationNotification).filter_by(kind=NotificationKind.SUPPORT_DIGEST).all()
    assert len(digest_rows) == 1
    assert "1 sync-age alarm" in digest_rows[0].summary  # aggregated count, never a per-tenant listing
