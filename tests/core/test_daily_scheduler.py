"""WP-6 PR-4: the cross-cutting daily-job scheduler — once-per-day,
retried-on-failure-not-tomorrow, riding the outbox-worker's own process.
KAN-303: a failing job is retried on a backoff, and alarms (an ERROR, which
the worker's Sentry turns into an event) once per failure episode.
"""

import datetime as dt
import itertools
import logging

import pytest

from app.core import daily_scheduler
from app.core.daily_scheduler import (
    _REGISTRY,
    REALARM_AFTER,
    RETRY_BACKOFF_CAP,
    RETRY_BACKOFF_FIRST,
    register_daily_job,
    reset_failure_episodes,
    run_due_daily_jobs,
)
from app.core.daily_scheduler_model import DailyJobRun

_T0 = dt.datetime(2026, 8, 31, 6, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry and the failure episodes are module-global — never let
    one test's registration or failures leak into another's."""

    _REGISTRY.clear()
    reset_failure_episodes()
    yield
    _REGISTRY.clear()
    reset_failure_episodes()


def _scheduler_records(caplog, level):
    return [r for r in caplog.records if r.name == "app.core.daily_scheduler" and r.levelno == level]


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


def test_a_failing_job_is_not_marked_done_and_is_retried_after_its_backoff_the_same_day(db_session):
    attempts = []

    def _flaky(db):
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("simulated transient failure")

    register_daily_job("flaky", _flaky)
    today = _T0.date()

    first = run_due_daily_jobs(db_session, today=today, now=_T0)
    assert first == []  # failed, never marked done
    assert db_session.query(DailyJobRun).filter_by(job_name="flaky", run_date=today).count() == 0

    # KAN-303: the next poll cycle (1 s later) does not run it again...
    assert run_due_daily_jobs(db_session, today=today, now=_T0 + dt.timedelta(seconds=1)) == []
    assert len(attempts) == 1

    # ...the first retry comes once the backoff has passed, the same day.
    second = run_due_daily_jobs(db_session, today=today, now=_T0 + RETRY_BACKOFF_FIRST)
    assert second == ["flaky"]
    assert len(attempts) == 2


def test_a_job_failing_on_every_poll_cycle_alarms_once_and_backs_off_exponentially(db_session, caplog):
    """KAN-303 exit criterion 2: N failures in a row are one ERROR (one Sentry
    event), not one per poll cycle; the retries double up to the cap and the
    job keeps being tried."""

    caplog.set_level(logging.INFO, logger="app.core.daily_scheduler")
    attempts: list[dt.datetime] = []
    now = _T0

    def _broken(db):
        attempts.append(now)
        raise RuntimeError("provider outage")

    register_daily_job("broken", _broken)

    # Every poll cycle (1 s) for ten hours, all on one day.
    end = _T0 + dt.timedelta(hours=10)
    while now < end:
        run_due_daily_jobs(db_session, today=_T0.date(), now=now)
        now += dt.timedelta(seconds=1)

    gaps = [later - earlier for earlier, later in itertools.pairwise(attempts)]
    assert gaps[:7] == [dt.timedelta(minutes=m) for m in (1, 2, 4, 8, 16, 32, 60)]
    assert all(gap <= RETRY_BACKOFF_CAP for gap in gaps)
    assert gaps[-1] == RETRY_BACKOFF_CAP  # still tried, on the cap, ten hours in
    assert len(attempts) < 20  # not 36,000

    [alarm] = _scheduler_records(caplog, logging.ERROR)
    assert alarm.jobName == "broken"
    assert alarm.exc_info is not None
    assert len(_scheduler_records(caplog, logging.WARNING)) == len(attempts) - 1


def test_a_failure_episode_that_lasts_a_day_alarms_again_once_a_day(db_session, caplog):
    caplog.set_level(logging.INFO, logger="app.core.daily_scheduler")

    def _broken(db):
        raise RuntimeError("still down")

    register_daily_job("broken", _broken)

    now = _T0
    end = _T0 + REALARM_AFTER * 2 + dt.timedelta(minutes=30)
    while now < end:
        run_due_daily_jobs(db_session, today=now.date(), now=now)
        now += dt.timedelta(minutes=1)

    alarms = _scheduler_records(caplog, logging.ERROR)
    assert len(alarms) == 3  # at the start, a day later, two days later


def test_a_job_that_recovers_ends_its_episode_and_its_next_failure_alarms_again(db_session, caplog):
    caplog.set_level(logging.INFO, logger="app.core.daily_scheduler")
    fail = {"on": True}

    def _job(db):
        if fail["on"]:
            raise RuntimeError("down")

    register_daily_job("job", _job)

    run_due_daily_jobs(db_session, today=_T0.date(), now=_T0)
    fail["on"] = False
    assert run_due_daily_jobs(db_session, today=_T0.date(), now=_T0 + RETRY_BACKOFF_FIRST) == ["job"]
    [recovered] = [r for r in _scheduler_records(caplog, logging.INFO) if r.getMessage() == "daily job recovered"]
    assert recovered.failedAttempts == 1

    # The next day's run fails again: a new episode, a new alarm, retried after the first backoff.
    next_day = _T0 + dt.timedelta(days=1)
    fail["on"] = True
    run_due_daily_jobs(db_session, today=next_day.date(), now=next_day)
    assert len(_scheduler_records(caplog, logging.ERROR)) == 2
    fail["on"] = False
    assert run_due_daily_jobs(db_session, today=next_day.date(), now=next_day + RETRY_BACKOFF_FIRST) == ["job"]


def test_a_backing_off_job_does_not_hold_back_a_healthy_job(db_session):
    calls = []
    register_daily_job("broken", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    register_daily_job("healthy", lambda db: calls.append("healthy"))

    assert run_due_daily_jobs(db_session, today=_T0.date(), now=_T0) == ["healthy"]
    assert run_due_daily_jobs(db_session, today=_T0.date(), now=_T0 + dt.timedelta(seconds=1)) == []
    next_day = _T0 + dt.timedelta(days=1)
    assert "healthy" in run_due_daily_jobs(db_session, today=next_day.date(), now=next_day)


def test_one_jobs_failure_does_not_stop_a_later_job_in_the_same_registry(db_session):
    calls = []
    register_daily_job("broken", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    register_daily_job("healthy", lambda db: calls.append("healthy"))

    ran = run_due_daily_jobs(db_session, today=dt.date(2026, 8, 31))

    assert ran == ["healthy"]
    assert calls == ["healthy"]


def test_the_backoff_counts_from_when_a_slow_job_failed_not_from_the_start_of_the_cycle(db_session, monkeypatch):
    """A job that runs for ten minutes before it raises must wait its full
    first backoff after the failure, not be retried on the next cycle
    because ten minutes have passed since the cycle began."""

    clock = {"now": _T0}
    monkeypatch.setattr(daily_scheduler, "utcnow", lambda: clock["now"])
    attempts = []

    def _slow_failure(db):
        attempts.append(clock["now"])
        clock["now"] += dt.timedelta(minutes=10)
        raise RuntimeError("timed out after ten minutes")

    register_daily_job("slow", _slow_failure)

    run_due_daily_jobs(db_session, today=_T0.date())  # fails at T0 + 10 min
    clock["now"] += dt.timedelta(seconds=1)
    run_due_daily_jobs(db_session, today=_T0.date())
    assert len(attempts) == 1  # the next cycle, a second after the failure, waits

    clock["now"] = _T0 + dt.timedelta(minutes=10) + RETRY_BACKOFF_FIRST
    run_due_daily_jobs(db_session, today=_T0.date())
    assert len(attempts) == 2
