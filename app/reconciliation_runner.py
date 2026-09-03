"""Composition root for nightly reconciliation (P-10) — runs every
context's job. Lives outside all bounded-context packages and app.core,
like app.model_registry and app.api.v1: it needs each context's specific
check list directly, not through a public.py (there's nothing generic
about "run this context's own checks" for another context to call).

platform has no outbound cross-context references (its only FK,
user.tenant_id, is intra-context), so there is no app.platform.reconciliation
and none is run here. aftersales, parts, finance, reporting and compliance
still need to add their own once they're built out.

WP-6 PR-1 also registers `app.valuation`'s own reconciliation module here
— it existed since WP-8 PR-5 but was never added to this list, a real,
pre-existing gap noticed and fixed in passing while wiring in this
package's own `app.integration.reconciliation`.
"""

import logging

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationAlarm, ReconciliationRun
from app.customer import reconciliation as customer_reconciliation
from app.integration import reconciliation as integration_reconciliation
from app.inventory import reconciliation as inventory_reconciliation
from app.sales import reconciliation as sales_reconciliation
from app.valuation import reconciliation as valuation_reconciliation
from app.vehicle import reconciliation as vehicle_reconciliation

logger = logging.getLogger("app.reconciliation_runner")

_JOBS = [
    customer_reconciliation,
    inventory_reconciliation,
    sales_reconciliation,
    valuation_reconciliation,
    vehicle_reconciliation,
    integration_reconciliation,
]


class MultiContextReconciliationAlarm(Exception):
    """Raised by run_all() when one or more contexts' jobs found orphans.
    Each wrapped ReconciliationAlarm already has its findings persisted —
    this is purely a reporting aggregate, nothing here writes anything.
    """

    def __init__(self, alarms: list[ReconciliationAlarm]) -> None:
        self.alarms = alarms
        total_orphans = sum(len(alarm.orphans) for alarm in alarms)
        contexts = ", ".join(alarm.run.context for alarm in alarms)
        super().__init__(f"{total_orphans} orphaned cross-context reference(s) found across: {contexts}.")


def run_all(db: Session) -> list[ReconciliationRun]:
    """Runs every context's reconciliation job, even if an earlier one finds
    orphans — a bad night for customer shouldn't hide a bad night for sales.
    Each job persists its own findings and raises ReconciliationAlarm before
    this function sees it; alarms are collected here and re-raised together
    as MultiContextReconciliationAlarm once every job has had its turn.
    """

    runs: list[ReconciliationRun] = []
    alarms: list[ReconciliationAlarm] = []
    for job in _JOBS:
        try:
            runs.append(job.run(db))
        except ReconciliationAlarm as exc:
            runs.append(exc.run)
            alarms.append(exc)

    if alarms:
        raise MultiContextReconciliationAlarm(alarms)
    return runs


def run_all_daily(db: Session) -> None:
    """The daily-job adapter for `run_all`, registered in
    `app/worker.py::register_daily_jobs` and run once per day on the
    outbox-worker process by `app.core.daily_scheduler`.

    A `MultiContextReconciliationAlarm` is a **finding, not a failure**:
    every orphan it carries is already persisted in `reconciliation_orphan`,
    and `run_all` has already given every context its turn. Letting it
    propagate would leave the job unmarked, so the scheduler would re-run
    six contexts' worth of full-table anti-joins every poll cycle (1s)
    until a human repairs a dangling id — the same trap
    `app/integration/daily_jobs.py` avoids for a stuck tenant. So it is
    logged at ERROR and swallowed here: the job is marked done for the
    day, and the finding surfaces through the log line, the
    `reconciliation_orphan` rows and `reconciliation_run.orphans_found`.

    Any **other** exception propagates — a transient database error should
    be retried on the next poll cycle, a finding should not.

    A run that alarms on nothing but also ran zero checks is treated as
    not-clean and logged at ERROR: a green signal with nothing behind it
    is the exact failure mode the WP-6 sync-age alarm exists to make loud.
    """

    try:
        runs = run_all(db)
    except MultiContextReconciliationAlarm as alarm:
        orphans_by_context = {a.run.context: len(a.orphans) for a in alarm.alarms}
        logger.error(
            "nightly reconciliation alarm: orphaned cross-context references found",
            extra={
                "orphansTotal": sum(orphans_by_context.values()),
                "orphansByContext": orphans_by_context,
            },
        )
        return

    checks_run = sum(run.checks_run for run in runs)
    if checks_run == 0:
        logger.error(
            "nightly reconciliation completed but executed zero checks",
            extra={"contextsChecked": [run.context for run in runs]},
        )
        return

    logger.info(
        "nightly reconciliation clean",
        extra={
            "contextsChecked": [run.context for run in runs],
            "checksRun": checks_run,
            "orphansTotal": 0,
        },
    )


__all__ = ["MultiContextReconciliationAlarm", "ReconciliationAlarm", "run_all", "run_all_daily"]
