"""Cross-cutting daily-job scheduler (WP-6 PR-4). Piggybacks on the
already-running, already-paid `dms-staging-outbox-worker` process rather
than a dedicated Render Cron Job service — a genuine, reversible cost/
complexity trade (WP-6 plan's own Reconciliation 5), not a silent default:
promoting to a dedicated cron service later is a small change to the same
entrypoint (`app/worker.py`'s own poll loop calls `run_due_daily_jobs`
once per cycle), never a rewrite.

No scheduling mechanism existed anywhere in this codebase before this —
`app/reconciliation_runner.py` is a plain library function with no
trigger of its own, a pre-existing, orthogonal gap this module does not
fix (flagged in the WP-6 plan's Open Items; wiring it in here later would
be natural, but is not this PR's job).

A job is claimed done for the day only AFTER it returns without raising —
see DailyJobRun's own docstring for why a failed attempt is retried on
the very next poll cycle rather than waiting until tomorrow.
"""

import datetime as dt
import logging
from collections.abc import Callable

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


def register_daily_job(name: str, job: DailyJob) -> None:
    _REGISTRY[name] = job


def registered_job_names() -> list[str]:
    """Test-only visibility into what's registered, without reaching into
    the private dict directly."""

    return list(_REGISTRY.keys())


def _has_run_today(db: Session, *, job_name: str, today: dt.date) -> bool:
    return (
        db.scalar(select(DailyJobRun.id).where(DailyJobRun.job_name == job_name, DailyJobRun.run_date == today))
        is not None
    )


def run_due_daily_jobs(db: Session, *, today: dt.date | None = None) -> list[str]:
    """Runs every registered job not yet marked done today, in
    registration order. Returns the names of jobs that ran (successfully)
    this call. A job that raises is logged and skipped — never marked
    done, so it is retried on the next call (the next poll cycle, 1s
    later in `app/worker.py`) — and never allowed to stop a later job in
    the same registry from running this same cycle.
    """

    today = today or utcnow().date()
    ran: list[str] = []
    for job_name, job in _REGISTRY.items():
        if _has_run_today(db, job_name=job_name, today=today):
            continue
        try:
            job(db)
            db.add(DailyJobRun(job_name=job_name, run_date=today))
            db.commit()
            ran.append(job_name)
        except Exception:
            db.rollback()
            logger.exception("daily job failed, will retry next poll cycle", extra={"jobName": job_name})
    return ran
