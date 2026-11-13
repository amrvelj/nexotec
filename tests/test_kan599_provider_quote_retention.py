"""KAN-599 (Anto, 2026-11-11): a provider quote has no maximum age — it may
back a valuation however old it is; the dialog's «Erneut holen» fetches a new
one when the seller wants it — and it is deleted ten years after it was
fetched by a daily job of the valuation context, with an audit row.

A quote a valuation references stays: the valuation keeps it as its provider
figure's source and is never edited (ADR-066).
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core import daily_scheduler
from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.valuation.daily_jobs import purge_expired_provider_quotes, quote_retention_cutoff
from app.valuation.models.valuation import ValuationProviderQuote
from app.worker import register_daily_jobs
from tests.test_valuation_provider_quote import (  # noqa: F401 — _fresh_breakers is an autouse fixture
    _connected,
    _fresh_breakers,
    _provider_configuration,
    _quote,
    _valuation,
)


def _fetched_ago(db_session, quote_id: str, **ago) -> None:
    stored = db_session.get(ValuationProviderQuote, uuid.UUID(quote_id))
    stored.fetched_at = utcnow() - dt.timedelta(**ago)
    db_session.commit()


def test_a_quote_years_old_still_backs_a_new_valuation(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()
    _fetched_ago(db_session, quote["id"], days=3 * 365)

    created = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"])

    assert created.status_code == 201, created.text
    assert created.json()["source"] == "auto_i_dat"
    assert created.json()["providerQuote"]["fetchedAt"].startswith(str((utcnow() - dt.timedelta(days=3 * 365)).year))


def test_the_daily_job_deletes_unused_quotes_older_than_ten_years_with_an_audit_row(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    expired = _quote(client, tenant_id, configuration_id).json()["id"]
    referenced = _quote(client, tenant_id, configuration_id).json()["id"]
    young = _quote(client, tenant_id, configuration_id).json()["id"]
    assert _valuation(client, tenant_id, configuration_id, providerQuoteId=referenced).status_code == 201
    _fetched_ago(db_session, expired, days=3653 + 1)
    _fetched_ago(db_session, referenced, days=3653 + 1)
    _fetched_ago(db_session, young, days=9 * 365)

    assert purge_expired_provider_quotes(db_session) == 1

    db_session.expire_all()
    left = set(db_session.scalars(select(ValuationProviderQuote.id)).all())
    assert left == {uuid.UUID(referenced), uuid.UUID(young)}
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "provider_quote_purged")).all()
    assert [(row.entity_id, row.tenant_id, row.reason) for row in audit] == [
        (uuid.UUID(expired), tenant_id, "retention_10_years")
    ]
    assert audit[0].before["configurationId"] == configuration_id
    assert "exchangePrice" not in audit[0].before

    # Run again the same day: nothing left to delete.
    assert purge_expired_provider_quotes(db_session) == 0


def test_the_cutoff_is_ten_calendar_years():
    assert quote_retention_cutoff(dt.datetime(2036, 11, 13, 4, 0, tzinfo=dt.UTC)) == dt.datetime(
        2026, 11, 13, 4, 0, tzinfo=dt.UTC
    )
    assert quote_retention_cutoff(dt.datetime(2036, 2, 29, tzinfo=dt.UTC)) == dt.datetime(2026, 2, 28, tzinfo=dt.UTC)


def test_the_worker_registers_the_quote_purge():
    saved = dict(daily_scheduler._REGISTRY)
    daily_scheduler._REGISTRY.clear()
    try:
        register_daily_jobs()
        names = daily_scheduler.registered_job_names()
    finally:
        daily_scheduler._REGISTRY.clear()
        daily_scheduler._REGISTRY.update(saved)
    assert "valuation.provider_quotes.purge" in names
