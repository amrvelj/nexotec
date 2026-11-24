"""KAN-738 (ADR-081 as amended by KAN-402; ADR-185): the object store
is the web service's disk, which no worker can reach, so the web service
produces and expires the data export (app/platform/administration/services/data_export_runner.py)
and the worker does neither.

The production-like setup: the web with the disk (the `export_store`
fixture is the API's store and the runner's), the worker with none (it
registers nothing that would open a store for an export). On Postgres.
"""

import datetime as dt
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.platform.administration.models.export_job import ExportJob, ExportJobStatus
from app.platform.administration.services import data_export, data_export_runner, stored_objects_check
from tests.data_export_support import (
    betrieb_with_manager,
    download,
    export_store_fixture,  # noqa: F401 - the fixture
    get_export,
    request_export,
)
from tests.postgres_only import ROW_LOCKS


class _RecordingTransport:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []

    def register(self, event_type: str, *, consumer_name: str, handler) -> None:
        self.registered.append((event_type, consumer_name))


def test_the_worker_neither_produces_nor_expires_an_export():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs, register_handlers

    transport = _RecordingTransport()
    register_handlers(transport)  # type: ignore[arg-type]
    register_daily_jobs()

    assert transport.registered, "the worker still registers its other consumers"
    assert [name for event, name in transport.registered if event == data_export.REQUESTED_EVENT] == []
    assert "platform.data_export_production" not in {name for _, name in transport.registered}
    assert "platform.data_exports.expire" not in registered_job_names()


def _logo_uploaded(client, dealership_id: str, headers: dict[str, str]) -> bytes:
    from tests.platform.test_branding import _png

    logo = _png(800, 300)
    response = client.put(
        f"/v1/dealerships/{dealership_id}/branding/assets/primary",
        content=logo,
        headers={**headers, "Content-Type": "image/png", "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    return logo


def test_the_web_runner_produces_a_complete_archive_from_the_webs_disk(client, export_store, engine):
    """The archive is written to the disk the download route reads, and it
    holds the uploaded logo — which a worker with a disk of its own would
    have silently left out."""

    dealership_id, _, headers = betrieb_with_manager(client)
    logo = _logo_uploaded(client, dealership_id, headers)
    job = request_export(client, dealership_id, headers)
    assert job["status"] == "requested"

    assert data_export_runner.run_once(sessionmaker(bind=engine, expire_on_commit=False), store=export_store) == 1

    ready = get_export(client, dealership_id, job["id"], headers)
    assert ready["status"] == "ready"
    archive = download(client, dealership_id, job["id"], headers)
    assert archive.read("files/branding/primary") == logo
    # A second pass finds nothing to do: produced once.
    assert data_export_runner.run_once(sessionmaker(bind=engine, expire_on_commit=False), store=export_store) == 0


@ROW_LOCKS
def test_an_export_another_pass_holds_is_skipped_and_produced_once_it_is_let_go(client, export_store, engine):
    """A pass that stops mid-production (a restart) holds the row until its
    transaction ends; the claim rolls back with it, so the export is still
    `requested` and the next pass produces it — once."""

    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    holder = factory()
    try:
        assert data_export_runner._claim_next(holder) == uuid.UUID(job["id"])
        assert data_export_runner.run_once(factory, store=export_store) == 0
    finally:
        holder.rollback()
        holder.close()
    assert get_export(client, dealership_id, job["id"], headers)["status"] == "requested"

    assert data_export_runner.run_once(factory, store=export_store) == 1
    assert get_export(client, dealership_id, job["id"], headers)["status"] == "ready"


def test_each_pass_deletes_a_file_whose_seven_days_have_passed(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    data_export_runner.run_once(factory, store=export_store)
    key = db_session.get(ExportJob, uuid.UUID(job["id"])).object_key
    assert key and export_store.get(key) is not None

    db_session.execute(
        update(ExportJob)
        .where(ExportJob.id == uuid.UUID(job["id"]))
        .values(download_expires_at=utcnow() - dt.timedelta(minutes=1))
    )
    db_session.commit()
    data_export_runner.run_once(factory, store=export_store)

    assert export_store.get(key) is None
    assert get_export(client, dealership_id, job["id"], headers)["status"] == "expired"


def test_a_request_wakes_the_runner(client, export_store, monkeypatch):
    woken: list[bool] = []
    monkeypatch.setattr(data_export_runner, "wake", lambda: woken.append(True))
    dealership_id, _, headers = betrieb_with_manager(client)

    request_export(client, dealership_id, headers)

    assert woken == [True]


def test_wake_without_a_runner_is_a_no_op():
    assert data_export_runner._runner is None
    data_export_runner.wake()


def test_the_web_service_starts_its_runner_and_stops_it_on_shutdown(monkeypatch):
    from app.db import SessionLocal
    from app.main import app

    calls: list[object] = []
    monkeypatch.setattr(data_export_runner, "start", lambda factory: calls.append(("start", factory)))
    monkeypatch.setattr(data_export_runner, "stop", lambda: calls.append("stop"))

    with TestClient(app):
        assert calls == [("start", SessionLocal)]
    assert calls == [("start", SessionLocal), "stop"]


def test_the_runner_thread_produces_a_request_it_is_woken_for(client, export_store, engine):
    """The real thread, started as app.main starts it, on a long interval:
    only the wake makes it look."""

    import time

    dealership_id, _, headers = betrieb_with_manager(client)
    data_export_runner.start(sessionmaker(bind=engine, expire_on_commit=False), interval=3600)
    try:
        job = request_export(client, dealership_id, headers)
        deadline = time.monotonic() + 30
        while get_export(client, dealership_id, job["id"], headers)["status"] == "requested":
            assert time.monotonic() < deadline, "the woken runner never produced the export"
            time.sleep(0.1)
    finally:
        data_export_runner.stop()
    assert get_export(client, dealership_id, job["id"], headers)["status"] == "ready"


def test_a_downloadable_export_whose_file_is_gone_is_a_start_up_finding(client, export_store, engine, db_session):
    """The criterion's decision: `export_job.object_key` is checked at
    start-up while the export is downloadable; an expired one holds no key
    and is not."""

    import pytest

    from app.core.reconciliation import ReconciliationAlarm

    dealership_id, _, headers = betrieb_with_manager(client)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    lost = request_export(client, dealership_id, headers)
    data_export_runner.run_once(factory, store=export_store)
    expired = request_export(client, dealership_id, headers)
    data_export_runner.run_once(factory, store=export_store)
    data_export.expire_downloads(db_session, now=utcnow() + dt.timedelta(days=8), store=export_store)  # both expire ...
    db_session.execute(
        update(ExportJob)
        .where(ExportJob.id == uuid.UUID(lost["id"]))
        .values(status=ExportJobStatus.READY, object_key=f"dealerships/{dealership_id}/exports/{lost['id']}.zip")
    )  # ... and one is put back as ready, its file gone (a deploy onto the wrong disk)
    db_session.commit()

    with pytest.raises(ReconciliationAlarm) as alarm:
        stored_objects_check.run(db_session, export_store)

    assert [(o.check_label, o.source_table, o.source_row_id) for o in alarm.value.orphans] == [
        ("export_job.object_key names no stored object", "export_job", uuid.UUID(lost["id"]))
    ]
    assert uuid.UUID(expired["id"]) not in {o.source_row_id for o in alarm.value.orphans}


# --- KAN-738 review: one failure never holds the others or the expiry back ------------


def test_an_export_that_escapes_production_is_left_requested_and_the_rest_still_run(
    client, export_store, engine, db_session, monkeypatch, caplog
):
    """`produce` records its own failures; one that still escapes it (the
    database, the commit) leaves that export `requested` for the next pass —
    and this pass still produces the others and runs the expiry."""

    dealership_id, _, headers = betrieb_with_manager(client)
    first = request_export(client, dealership_id, headers)
    second = request_export(client, dealership_id, headers)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    expired: list[bool] = []
    real_produce, real_expire = data_export.produce, data_export.expire_downloads

    def produce(db, *, export_id, store=None):
        if str(export_id) == first["id"]:
            raise RuntimeError("the database went away")
        return real_produce(db, export_id=export_id, store=store)

    def expire(db, *, now=None, store=None):
        expired.append(True)
        return real_expire(db, now=now, store=store)

    monkeypatch.setattr(data_export, "produce", produce)
    monkeypatch.setattr(data_export, "expire_downloads", expire)

    assert data_export_runner.run_once(factory, store=export_store) == 1

    assert get_export(client, dealership_id, first["id"], headers)["status"] == "requested"
    assert get_export(client, dealership_id, second["id"], headers)["status"] == "ready"
    assert expired == [True]
    assert any("left requested for the next pass" in record.getMessage() for record in caplog.records)

    monkeypatch.setattr(data_export, "produce", real_produce)
    assert data_export_runner.run_once(factory, store=export_store) == 1
    assert get_export(client, dealership_id, first["id"], headers)["status"] == "ready"


def test_a_failing_expiry_is_logged_and_never_raises(client, export_store, engine, monkeypatch, caplog):
    def expire(db, *, now=None, store=None):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(data_export, "expire_downloads", expire)

    assert data_export_runner.run_once(sessionmaker(bind=engine, expire_on_commit=False), store=export_store) == 0
    assert any("the expiry pass failed" in record.getMessage() for record in caplog.records)


@ROW_LOCKS
def test_the_nightly_reconciliation_reports_a_stuck_request_and_an_overdue_file(
    client, export_store, engine, db_session
):
    """Rule 10: what the outbox's dead letter and the daily job's alarm said
    before — an export still `requested` an hour on, a file kept an hour past
    its seven days."""

    from app.core.reconciliation import StateCheck, find_rows_in_state
    from app.platform import reconciliation

    dealership_id, _, headers = betrieb_with_manager(client)
    stuck = request_export(client, dealership_id, headers)
    overdue = request_export(client, dealership_id, headers)
    fresh = request_export(client, dealership_id, headers)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db_session.execute(
        update(ExportJob)
        .where(ExportJob.id.in_([uuid.UUID(stuck["id"]), uuid.UUID(overdue["id"])]))
        .values(created_at=utcnow() - dt.timedelta(hours=2))
    )
    db_session.commit()
    # Produce `overdue` only, then age its window; `stuck` stays requested.
    holder = factory()
    holder.execute(
        update(ExportJob).where(ExportJob.id == uuid.UUID(stuck["id"])).values(reason="held")
    )  # a row lock: the pass skips it
    try:
        data_export_runner.run_once(factory, store=export_store)
    finally:
        holder.rollback()
        holder.close()
    db_session.execute(
        update(ExportJob)
        .where(ExportJob.id == uuid.UUID(overdue["id"]))
        .values(download_expires_at=utcnow() - dt.timedelta(hours=2))
    )
    db_session.commit()

    by_label = {
        check.label: check
        for check in reconciliation.CHECKS
        if isinstance(check, StateCheck) and check.label.startswith("export_job.")
    }

    assert [
        row for row, _ in find_rows_in_state(db_session, by_label["export_job.requested_an_hour_ago_and_not_produced"])
    ] == [uuid.UUID(stuck["id"])]
    assert [
        row for row, _ in find_rows_in_state(db_session, by_label["export_job.file_kept_past_its_download_window"])
    ] == [uuid.UUID(overdue["id"])]
    assert uuid.UUID(fresh["id"]) not in {
        row for check in by_label.values() for row, _ in find_rows_in_state(db_session, check)
    }
