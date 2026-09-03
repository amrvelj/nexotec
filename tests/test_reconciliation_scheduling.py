"""The nightly reconciliation is actually scheduled (P-10, CLAUDE.md rule
10). `app.reconciliation_runner.run_all` was correct and tested since PR-2
but nothing triggered it — its only caller was a test. There are no
cross-context foreign keys anywhere in this codebase, so this job is the
whole compensating control, and it had never run.

These tests pin three things: the job is registered by name (so deleting
the registration fails the build), a real orphan alarms without taking the
worker down, and a clean run is durably distinguishable from "never ran".

Not gated behind any env-var skipif — a silently skipped test on the
criterion that closes the ticket is a known failure mode on this project.
"""

import datetime as dt
import uuid

import pytest

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names, run_due_daily_jobs
from app.core.daily_scheduler_model import DailyJobRun
from app.core.reconciliation import seconds_since_last_reconciliation
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.reconciliation_runner import run_all_daily

_RECONCILIATION_JOB_NAME = "reconciliation.run_all"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The scheduler registry is module-global — never let one test's
    registration leak into another's (mirrors tests/test_daily_scheduler.py)."""

    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def _orphan_vehicle_party(db_session) -> None:
    """One VehicleParty whose vehicle_id resolves to nothing — a dangling
    customer -> vehicle reference, the exact drift the dropped FK used to
    make impossible."""

    db_session.add(
        VehicleParty(vehicle_id=uuid.uuid4(), customer_id=uuid.uuid4(), role=VehiclePartyRole.OWNER)
    )
    db_session.commit()


def test_worker_registers_the_reconciliation_job_by_name():
    """Asserted BY NAME so that deleting the registration in
    app/worker.py fails the build rather than silently disarming the only
    cross-context integrity check."""

    worker.register_daily_jobs()

    names = registered_job_names()
    assert _RECONCILIATION_JOB_NAME in names
    # The pre-existing job must survive this change.
    assert "integration.daily_jobs" in names


def test_an_orphan_alarms_and_the_worker_survives(db_session):
    worker.register_daily_jobs()
    _orphan_vehicle_party(db_session)
    today = dt.date(2026, 9, 4)

    # run_due_daily_jobs is what the worker loop calls every poll cycle; it
    # must not raise, and the job must be marked done (not left to re-run
    # every 1s until someone repairs the id).
    ran = run_due_daily_jobs(db_session, today=today)

    assert _RECONCILIATION_JOB_NAME in ran
    assert (
        db_session.query(DailyJobRun)
        .filter_by(job_name=_RECONCILIATION_JOB_NAME, run_date=today)
        .count()
        == 1
    )

    # The finding is durable: run_all persisted it before the alarm was
    # ever raised.
    orphans = db_session.query(ReconciliationOrphan).filter_by(context="customer").all()
    assert len(orphans) == 1
    assert orphans[0].check_label == "vehicle_party.vehicle_id -> vehicle_mdm.id"

    # And it is not re-run the same day.
    assert run_due_daily_jobs(db_session, today=today) == []


def test_run_all_daily_swallows_the_alarm_rather_than_raising(db_session):
    _orphan_vehicle_party(db_session)

    # Directly, without the scheduler: a MultiContextReconciliationAlarm is
    # a finding, not a failure — it must not propagate out of the adapter
    # (which would kill the worker loop / trigger a 1s retry storm).
    assert run_all_daily(db_session) is None

    assert db_session.query(ReconciliationOrphan).count() == 1


def test_a_clean_run_is_recorded_distinguishably_from_no_run(db_session):
    # "Never ran" — nothing has written a reconciliation_run row.
    assert db_session.query(ReconciliationRun).count() == 0
    assert seconds_since_last_reconciliation(db_session) is None

    run_all_daily(db_session)

    # "Ran and clean" — one row per context, every one recording zero
    # orphans and a non-zero check count (a run that verified nothing would
    # be the dangerous "green with nothing behind it" case).
    runs = db_session.query(ReconciliationRun).all()
    assert {r.context for r in runs} == {
        "customer",
        "inventory",
        "sales",
        "valuation",
        "vehicle",
        "integration",
    }
    assert all(r.orphans_found == 0 for r in runs)
    assert sum(r.checks_run for r in runs) > 0

    age = seconds_since_last_reconciliation(db_session)
    assert age is not None and age >= 0
