"""Composition root for nightly reconciliation (P-10) — runs every
context's job. Lives outside all ten context packages and app.core, like
app.model_registry and app.api.v1: it needs each context's specific check
list directly, not through a public.py (there's nothing generic about
"run this context's own checks" for another context to call).

platform has no outbound cross-context references (its only FK,
user.tenant_id, is intra-context), so there is no app.platform.reconciliation
and none is run here. Whichever work package first builds inventory,
aftersales, parts, finance, reporting or compliance out with cross-context
references must add its own reconciliation module and wire it in here.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationAlarm, ReconciliationRun
from app.customer import reconciliation as customer_reconciliation
from app.sales import reconciliation as sales_reconciliation
from app.vehicle import reconciliation as vehicle_reconciliation

_JOBS = [customer_reconciliation, sales_reconciliation, vehicle_reconciliation]


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


__all__ = ["MultiContextReconciliationAlarm", "ReconciliationAlarm", "run_all"]
