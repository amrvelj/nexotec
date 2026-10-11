"""Cross-cutting daily-job scheduler (WP-6 PR-4). Piggybacks on the outbox
worker process (`python -m app.worker`), which runs anyway, rather than a
dedicated cron service — a genuine, reversible cost/complexity trade
(WP-6 plan's own Reconciliation 5), not a silent default: promoting to a
dedicated cron service later is a small change to the same entrypoint
(`app/worker.py`'s own poll loop calls `run_due_daily_jobs` once per
cycle), never a rewrite. The worker runs wherever it is provisioned:
docker-compose's `worker` service locally; on Render's staging it is
declared in render.yaml but commented out and not provisioned (KAN-234,
ADR-081), so there these jobs do not run at all.

No scheduling mechanism existed anywhere in this codebase before this —
`app/reconciliation_runner.py` is a plain library function with no
trigger of its own, a pre-existing, orthogonal gap this module does not
fix (flagged in the WP-6 plan's Open Items; wiring it in here later would
be natural, but is not this PR's job).

A job is claimed done for the day only AFTER it returns without raising —
see DailyJobRun's own docstring for why a failed attempt is retried the
same day rather than waiting until tomorrow.

KAN-303 — a failing job is retried on a backoff, not on every poll cycle,
and alarms once per failure episode. Since KAN-248 an ERROR line in the
worker is a Sentry event, so retrying every 1 s poll cycle and logging
ERROR each time sent ~86,400 events a day per failing job. Now: the first
failure of an episode logs ERROR (the alarm); each retry that fails again
logs WARNING (Sentry keeps it as a breadcrumb only) and doubles the wait,
from RETRY_BACKOFF_FIRST up to RETRY_BACKOFF_CAP; an episode still failing
REALARM_AFTER after its last alarm alarms again, so an outage that lasts
days stays visible once a day; the first success ends the episode with an
INFO "daily job recovered". The episode lives in this process's memory,
not the database: a worker restart starts a fresh episode (one more
alarm, an immediate retry), which errs on the side of being told.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.daily_scheduler_model import DailyJobRun

logger = logging.getLogger("app.core.daily_scheduler")

DailyJob = Callable[[Session], None]

# Registration order is run order — deliberate, so a caller can register a
# job that depends on an earlier one having already run today (PR-6's
# purge/notification jobs, for instance, running after PR-4's sync job).
_REGISTRY: dict[str, DailyJob] = {}

RETRY_BACKOFF_FIRST = dt.timedelta(minutes=1)
RETRY_BACKOFF_CAP = dt.timedelta(hours=1)
REALARM_AFTER = dt.timedelta(hours=24)


@dataclass
class _FailureEpisode:
    started_at: dt.datetime
    last_alarm_at: dt.datetime
    failed_attempts: int
    backoff: dt.timedelta
    next_attempt_at: dt.datetime


_EPISODES: dict[str, _FailureEpisode] = {}


def register_daily_job(name: str, job: DailyJob) -> None:
    _REGISTRY[name] = job


def registered_job_names() -> list[str]:
    """Test-only visibility into what's registered, without reaching into
    the private dict directly."""

    return list(_REGISTRY.keys())


def reset_failure_episodes() -> None:
    """Test-only: forget every job's failure episode (module-global, like
    the registry)."""

    _EPISODES.clear()


def _record_failure(job_name: str, now: dt.datetime) -> tuple[bool, str, dict[str, object]]:
    """Advances the job's failure episode and returns how to log this
    failure: (alarm?, message, extra). The caller logs it inside its
    `except`, so the line carries the traceback."""

    episode = _EPISODES.get(job_name)
    if episode is None:
        _EPISODES[job_name] = _FailureEpisode(
            started_at=now,
            last_alarm_at=now,
            failed_attempts=1,
            backoff=RETRY_BACKOFF_FIRST,
            next_attempt_at=now + RETRY_BACKOFF_FIRST,
        )
        extra: dict[str, object] = {"jobName": job_name, "retryInSeconds": RETRY_BACKOFF_FIRST.total_seconds()}
        return True, "daily job failed, will retry with backoff", extra

    episode.failed_attempts += 1
    # Doubled from the stored wait, never from 2**attempts: that overflows
    # timedelta after a few dozen failures and would crash the worker loop.
    backoff = episode.backoff = min(episode.backoff * 2, RETRY_BACKOFF_CAP)
    episode.next_attempt_at = now + backoff
    extra = {
        "jobName": job_name,
        "failedAttempts": episode.failed_attempts,
        "failingSince": episode.started_at.isoformat(),
        "retryInSeconds": backoff.total_seconds(),
    }
    if now - episode.last_alarm_at >= REALARM_AFTER:
        episode.last_alarm_at = now
        return True, "daily job still failing", extra
    return False, "daily job failed again, will retry with backoff", extra


def _record_success(job_name: str, now: dt.datetime) -> None:
    episode = _EPISODES.pop(job_name, None)
    if episode is not None:
        logger.info(
            "daily job recovered",
            extra={
                "jobName": job_name,
                "failedAttempts": episode.failed_attempts,
                "failingSince": episode.started_at.isoformat(),
                "failingForSeconds": (now - episode.started_at).total_seconds(),
            },
        )


def _has_run_today(db: Session, *, job_name: str, today: dt.date) -> bool:
    return (
        db.scalar(select(DailyJobRun.id).where(DailyJobRun.job_name == job_name, DailyJobRun.run_date == today))
        is not None
    )


def run_due_daily_jobs(
    db: Session, *, today: dt.date | None = None, now: dt.datetime | None = None
) -> list[str]:
    """Runs every registered job not yet marked done today, in
    registration order. Returns the names of jobs that ran (successfully)
    this call. A job that raises is logged and skipped — never marked
    done, so it is retried later the same day, once its backoff has passed
    (KAN-303, module docstring) — and never allowed to stop a later job in
    the same registry from running this same cycle.
    """

    now = now or utcnow()
    today = today or now.date()
    ran: list[str] = []
    for job_name, job in _REGISTRY.items():
        episode = _EPISODES.get(job_name)
        if episode is not None and now < episode.next_attempt_at:
            continue
        if _has_run_today(db, job_name=job_name, today=today):
            continue
        try:
            job(db)
            db.add(DailyJobRun(job_name=job_name, run_date=today))
            db.commit()
        except Exception:
            db.rollback()
            alarm, message, extra = _record_failure(job_name, now)
            if alarm:
                logger.exception(message, extra=extra)
            else:
                logger.warning(message, exc_info=True, extra=extra)
            continue
        ran.append(job_name)
        _record_success(job_name, now)
    return ran
