"""KAN-599 (Anto, 2026-11-13: option A) — a provider quote stays as long as
the valuation that references it; one no valuation references is deleted ten
years after it was fetched, by the daily valuation job, with an audit row.
And (Anto, 2026-11-11) no maximum age: a quote of any age backs a new
valuation.

The job runs as the worker runs it (`run_daily_unused_provider_quote_purge`);
the "no maximum age" pin goes through `POST /v1/valuations` on the demo
provider, as KAN-552's own tests do.
"""

import datetime as dt
import logging
import threading
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.valuation import daily_jobs
from app.valuation.daily_jobs import run_daily_unused_provider_quote_purge
from app.valuation.models.valuation import (
    ResidualTableState,
    Valuation,
    ValuationProviderQuote,
    ValuationSource,
)
from app.valuation.services import provider_quote_retention
from app.valuation.services.provider_quote_retention import (
    PURGE_REASON,
    RETENTION_YEARS,
    purge_unused_quotes,
    retention_cutoff,
)
from app.worker import register_daily_jobs
from tests.postgres_only import ROW_LOCKS
from tests.test_valuation_provider_quote import (
    _connected,
    _fresh_breakers,  # noqa: F401 - autouse fixture, reset after each test here too
    _provider_configuration,
    _quote,
    _valuation,
)

# A purge run "now"; quotes are aged relative to it.
NOW = dt.datetime(2036, 11, 13, 3, 0, tzinfo=dt.UTC)


# --- helpers ------------------------------------------------------------------


def _stored_quote(db_session, tenant_id: uuid.UUID, *, fetched_at: dt.datetime) -> uuid.UUID:
    quote = ValuationProviderQuote(
        tenant_id=tenant_id,
        configuration_id=uuid.uuid4(),
        fz_key="FZ100001",
        model_year=2020,
        first_registration=dt.date(2020, 3, 2),
        mileage=41000,
        option_keys=[912001],
        exchange_price=Decimal("18000.00"),
        residual_table_state=ResidualTableState.PRESENT,
        residual_table=[],
        provider_request_ref=uuid.uuid4(),
        fetched_at=fetched_at,
        created_at=fetched_at,
    )
    db_session.add(quote)
    db_session.commit()
    return quote.id


def _valuation_on(db_session, quote_id: uuid.UUID, *, is_draft: bool = False) -> uuid.UUID:
    quote = db_session.get(ValuationProviderQuote, quote_id)
    valuation = Valuation(
        tenant_id=quote.tenant_id,
        valuation_number=f"B-{uuid.uuid4().hex[:6]}",
        source=ValuationSource.AUTO_I_DAT,
        provider_value=quote.exchange_price,
        final_offer=Decimal("15000.00"),
        valid_from=quote.fetched_at.date(),
        valid_until=quote.fetched_at + dt.timedelta(days=30),
        is_draft=is_draft,
        provider_quote_id=quote_id,
    )
    db_session.add(valuation)
    db_session.commit()
    return valuation.id


def _exists(db_session, quote_id: uuid.UUID) -> bool:
    db_session.expire_all()
    return db_session.get(ValuationProviderQuote, quote_id) is not None


def _purge_audits(db_session, quote_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "valuation_provider_quote",
                AuditEvent.entity_id == quote_id,
                AuditEvent.reason == PURGE_REASON,
            )
        )
    )


def _run_job(db_session, monkeypatch) -> None:
    monkeypatch.setattr(provider_quote_retention, "utcnow", lambda: NOW)
    run_daily_unused_provider_quote_purge(db_session)


# --- the retention period -----------------------------------------------------


def test_the_retention_is_ten_calendar_years_and_never_less():
    assert RETENTION_YEARS == 10
    assert retention_cutoff(NOW) == dt.datetime(2026, 11, 13, 3, 0, tzinfo=dt.UTC)
    # 29 February has no twin ten years back: the cutoff is the 28th, earlier,
    # so a quote is never deleted before it is ten years old.
    leap = dt.datetime(2032, 2, 29, 12, 0, tzinfo=dt.UTC)
    assert retention_cutoff(leap) == dt.datetime(2022, 2, 28, 12, 0, tzinfo=dt.UTC)


# --- unused: deleted after ten years, with an audit row -----------------------


def test_an_unused_quote_past_ten_years_is_deleted_with_an_audit_row_in_its_own_betrieb(
    db_session, monkeypatch, caplog
):
    first, second = uuid.uuid4(), uuid.uuid4()
    old_first = _stored_quote(db_session, first, fetched_at=retention_cutoff(NOW) - dt.timedelta(days=1))
    old_second = _stored_quote(db_session, second, fetched_at=retention_cutoff(NOW))  # exactly ten years

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        _run_job(db_session, monkeypatch)

    for quote_id, tenant_id in ((old_first, first), (old_second, second)):
        assert not _exists(db_session, quote_id)
        [audit] = _purge_audits(db_session, quote_id)
        assert audit.action == "delete" and audit.actor_id is None
        assert audit.tenant_id == tenant_id, "the audit row belongs to the quote's own Betrieb"
        assert set(audit.before) == {"configurationId", "providerRequestRef", "fetchedAt"}, (
            "the audit row must not keep the mileage, first registration, FzKey or option keys"
        )
        assert audit.after is None
    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.getMessage() == "valuation.provider_quotes.purge_unused: purged=2"


def test_an_unused_quote_under_ten_years_stays(db_session, monkeypatch):
    young = _stored_quote(db_session, uuid.uuid4(), fetched_at=retention_cutoff(NOW) + dt.timedelta(seconds=1))

    _run_job(db_session, monkeypatch)

    assert _exists(db_session, young)
    assert _purge_audits(db_session, young) == []


def test_a_repeat_run_finds_nothing_to_do(db_session, monkeypatch):
    _stored_quote(db_session, uuid.uuid4(), fetched_at=retention_cutoff(NOW) - dt.timedelta(days=400))
    _run_job(db_session, monkeypatch)
    audits = db_session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.reason == PURGE_REASON))

    assert purge_unused_quotes(db_session, now=NOW).purged == 0
    assert db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.reason == PURGE_REASON)
    ) == audits == 1


# --- used: stays as long as its valuation (option A) --------------------------


@pytest.mark.parametrize("is_draft", [False, True], ids=["valuation", "draft-valuation"])
def test_a_quote_a_valuation_references_stays_however_old(db_session, monkeypatch, is_draft):
    tenant_id = uuid.uuid4()
    used = _stored_quote(db_session, tenant_id, fetched_at=retention_cutoff(NOW) - dt.timedelta(days=3650))
    valuation_id = _valuation_on(db_session, used, is_draft=is_draft)

    _run_job(db_session, monkeypatch)

    assert _exists(db_session, used)
    assert _purge_audits(db_session, used) == []
    assert db_session.get(Valuation, valuation_id).provider_quote_id == used


def test_the_database_refuses_to_delete_a_quote_a_valuation_references(db_session):
    """The backstop under the job: `fk_valuation_provider_quote_id` has no
    ON DELETE, so a referenced quote cannot go, whatever the job reads."""

    used = _stored_quote(db_session, uuid.uuid4(), fetched_at=retention_cutoff(NOW) - dt.timedelta(days=1))
    _valuation_on(db_session, used)

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM valuation_provider_quote WHERE id = :id"), {"id": used})
        db_session.flush()
    db_session.rollback()
    assert _exists(db_session, used)


def test_batches_delete_every_unused_quote_and_keep_every_used_one(db_session):
    tenant_id = uuid.uuid4()
    old = retention_cutoff(NOW) - dt.timedelta(days=10)
    unused = [_stored_quote(db_session, tenant_id, fetched_at=old + dt.timedelta(minutes=i)) for i in range(5)]
    used = [_stored_quote(db_session, tenant_id, fetched_at=old + dt.timedelta(seconds=30 + 60 * i)) for i in range(3)]
    for quote_id in used:
        _valuation_on(db_session, quote_id)

    result = purge_unused_quotes(db_session, now=NOW, batch_size=2)

    assert result.purged == 5
    assert not any(_exists(db_session, quote_id) for quote_id in unused)
    assert all(_exists(db_session, quote_id) for quote_id in used)


@ROW_LOCKS
def test_a_quote_a_valuation_create_is_holding_is_skipped(engine, db_session):
    """A valuation insert holds its quote's key-share lock (the foreign key)
    until it commits: the job skips that quote rather than wait for it, and
    deletes the others."""

    tenant_id = uuid.uuid4()
    old = retention_cutoff(NOW) - dt.timedelta(days=1)
    held = _stored_quote(db_session, tenant_id, fetched_at=old)
    free = _stored_quote(db_session, tenant_id, fetched_at=old)

    holder = sessionmaker(bind=engine)()
    try:
        holder.execute(text("SELECT id FROM valuation_provider_quote WHERE id = :id FOR KEY SHARE"), {"id": held})
        job_db = sessionmaker(bind=engine)()
        try:
            # A job that waited would fail here instead of hanging the test.
            job_db.execute(text("SET lock_timeout = '3s'"))
            assert purge_unused_quotes(job_db, now=NOW).purged == 1
        finally:
            job_db.close()
    finally:
        holder.rollback()
        holder.close()

    assert _exists(db_session, held) and not _exists(db_session, free)
    assert purge_unused_quotes(db_session, now=NOW).purged == 1  # the next night


def _wait_until_a_backend_waits_on_a_lock(engine, *, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            if waiting:
                return
            watcher.rollback()
            time.sleep(0.05)
    raise AssertionError("the valuation create never waited for the purge's lock")


@ROW_LOCKS
def test_a_valuation_created_on_a_quote_the_purge_is_deleting_is_refused_not_a_500(client, engine, db_session):
    """KAN-703 — the race KAN-599 left open: the job has locked and deleted an
    unused ten-year-old quote, not yet committed, when a `POST /v1/valuations`
    names it. Before the fix the create read the quote (the delete was not
    committed), built the valuation, and its insert failed on the foreign key
    once the job committed: an unhandled IntegrityError, a 500. Now the create
    reads the quote `FOR KEY SHARE`, waits for the job, finds the quote gone
    and answers the 404 any vanished quote gets; nothing is stored."""

    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()
    quote_id = uuid.UUID(quote["id"])
    ten_years_ago = retention_cutoff(utcnow()) - dt.timedelta(days=1)
    db_session.execute(
        update(ValuationProviderQuote)
        .where(ValuationProviderQuote.id == quote_id)
        .values(fetched_at=ten_years_ago, created_at=ten_years_ago)
    )
    db_session.commit()

    job_db = sessionmaker(bind=engine)()
    outcome: dict[str, object] = {}
    try:
        # The job's own statements: lock the unreferenced quote, delete it.
        job_db.execute(
            text("SELECT id FROM valuation_provider_quote WHERE id = :id FOR UPDATE SKIP LOCKED"), {"id": quote_id}
        )
        job_db.execute(text("DELETE FROM valuation_provider_quote WHERE id = :id"), {"id": quote_id})

        def _create() -> None:
            try:
                outcome["response"] = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"])
            except Exception as exc:  # the unfixed create raises out of the TestClient
                outcome["error"] = exc

        creator = threading.Thread(target=_create)
        creator.start()
        _wait_until_a_backend_waits_on_a_lock(engine)
        job_db.commit()
        creator.join(timeout=15)
        assert not creator.is_alive(), "the valuation create hung"
    finally:
        job_db.rollback()
        job_db.close()

    assert "error" not in outcome, f"the create failed unhandled: {outcome.get('error')!r}"
    response = outcome["response"]
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Valuation).where(Valuation.tenant_id == tenant_id)) == 0


# --- no maximum age (Anto, 2026-11-11) ------------------------------------------


@pytest.mark.parametrize(
    "age",
    [dt.timedelta(days=400), dt.timedelta(days=3652 - 1), dt.timedelta(days=3652 + 30)],
    ids=["a-year", "just-under-ten-years", "past-ten-years-not-yet-purged"],
)
def test_a_quote_of_any_age_backs_a_new_auto_i_dat_valuation(client, db_session, age):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()
    fetched_long_ago = utcnow() - age
    db_session.execute(
        update(ValuationProviderQuote)
        .where(ValuationProviderQuote.id == uuid.UUID(quote["id"]))
        .values(fetched_at=fetched_long_ago, created_at=fetched_long_ago)
    )
    db_session.commit()

    created = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"])

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "auto_i_dat" and body["providerQuote"]["id"] == quote["id"]
    assert Decimal(body["providerValue"]) == Decimal(quote["exchangePrice"])


# --- the worker runs it ---------------------------------------------------------


def test_the_job_is_registered_before_the_configuration_purge():
    from app.core import daily_scheduler

    saved = dict(daily_scheduler._REGISTRY)
    daily_scheduler._REGISTRY.clear()
    try:
        register_daily_jobs()
        names = daily_scheduler.registered_job_names()
    finally:
        daily_scheduler._REGISTRY.clear()
        daily_scheduler._REGISTRY.update(saved)
    assert daily_scheduler._REGISTRY == saved
    assert "valuation.provider_quotes.purge_unused" in names
    # KAN-270's purge stays last (test_vehicle_configuration_retention.py).
    assert names[-1] == "vehicle.configurations.purge_unattached"
    assert names.index("valuation.provider_quotes.purge_unused") == len(names) - 2
