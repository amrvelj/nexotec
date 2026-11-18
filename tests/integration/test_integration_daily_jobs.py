"""WP-6 PR-6: the daily-job composition root — expiry warnings, retention
purge and the support digest. Until KAN-485 (2026-10-29) it also ran every
tenant's catalogue delta and the sync-age alarm; no provider is asked for
its whole catalogue any more, and
`tests/architecture/test_no_bulk_catalogue_retrieval.py` keeps every daily
job away from the provider.
"""

import datetime as dt
import uuid

from app.integration import daily_jobs
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import ModelVariant


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


def test_run_daily_integration_jobs_leaves_the_catalogue_alone(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    daily_jobs.run_daily_integration_jobs(db_session)

    # The warnings, purge and digest steps ran without raising; no catalogue
    # sync ran, so nothing was fetched.
    assert db_session.query(ModelVariant).count() == 0


def test_run_daily_retention_purge_reports_zero_on_a_clean_database(db_session):
    counts = daily_jobs.run_daily_retention_purge(db_session)
    assert counts == {"callLogMetadata": 0, "errorPayloads": 0, "successPayloads": 0}


def test_run_daily_integration_jobs_sends_a_digest_when_an_expiry_warning_fires(db_session, monkeypatch):
    sentinel = IntegrationNotification(kind=NotificationKind.EXPIRY_WARNING, sent_date=dt.date(2026, 8, 31))
    monkeypatch.setattr(daily_jobs.notification_service, "check_and_send_expiry_warnings", lambda db: [sentinel])

    daily_jobs.run_daily_integration_jobs(db_session)

    digest_rows = db_session.query(IntegrationNotification).filter_by(kind=NotificationKind.SUPPORT_DIGEST).all()
    assert len(digest_rows) == 1
    assert "1 expiry warning" in digest_rows[0].summary  # aggregated count, never a per-tenant listing
    assert "sync-age" not in digest_rows[0].summary
