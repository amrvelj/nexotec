"""WP-6 PR-4: the cross-cutting daily-job scheduler — once-per-day,
retried-on-failure-not-tomorrow, riding the outbox-worker's own process.
"""

import datetime as dt

import pytest

from app.core.daily_scheduler import _REGISTRY, register_daily_job, run_due_daily_jobs
from app.core.daily_scheduler_model import DailyJobRun


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module-global — never let one test's registration
    leak into another's."""

    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_a_registered_job_runs_once_and_is_marked_done_for_the_day(db_session):
    calls = []
    register_daily_job("demo", lambda db: calls.append(1))

    ran = run_due_daily_jobs(db_session, today=dt.date(2026, 8, 31))

    assert ran == ["demo"]
    assert calls == [1]
    assert db_session.query(DailyJobRun).filter_by(job_name="demo", run_date=dt.date(2026, 8, 31)).count() == 1


def test_a_job_already_marked_done_today_does_not_run_again(db_session):
    calls = []
    register_daily_job("demo", lambda db: calls.append(1))
    today = dt.date(2026, 8, 31)

    run_due_daily_jobs(db_session, today=today)
    ran_again = run_due_daily_jobs(db_session, today=today)

    assert ran_again == []
    assert calls == [1]


def test_a_job_runs_again_on_a_new_day(db_session):
    calls = []
    register_daily_job("demo", lambda db: calls.append(1))

    run_due_daily_jobs(db_session, today=dt.date(2026, 8, 31))
    ran_next_day = run_due_daily_jobs(db_session, today=dt.date(2026, 9, 1))

    assert ran_next_day == ["demo"]
    assert calls == [1, 1]


def test_a_failing_job_is_not_marked_done_and_is_retried_next_call(db_session):
    attempts = []

    def _flaky(db):
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("simulated transient failure")

    register_daily_job("flaky", _flaky)
    today = dt.date(2026, 8, 31)

    first = run_due_daily_jobs(db_session, today=today)
    assert first == []  # failed, never marked done
    assert db_session.query(DailyJobRun).filter_by(job_name="flaky", run_date=today).count() == 0

    second = run_due_daily_jobs(db_session, today=today)
    assert second == ["flaky"]  # retried on the very next call, same day
    assert len(attempts) == 2


def test_one_jobs_failure_does_not_stop_a_later_job_in_the_same_registry(db_session):
    calls = []
    register_daily_job("broken", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    register_daily_job("healthy", lambda db: calls.append("healthy"))

    ran = run_due_daily_jobs(db_session, today=dt.date(2026, 8, 31))

    assert ran == ["healthy"]
    assert calls == ["healthy"]
