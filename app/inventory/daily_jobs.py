"""The daily job `app/worker.py` registers for inventory (KAN-122): the
orphan-reservation sweep (app.inventory.services.reservation_sweep). A
context-level module, like app.integration.daily_jobs, so the worker never
reaches into this context's services.

One log line per run with its counts — the record Anto asked for
(2026-10-07). The counts are in the message text itself: the worker sets up
logging with a format that has no `extra` fields (app/worker.py), so they
would be dropped; they are passed as `extra` too, for a structured formatter.
A failed release is logged at ERROR — the alarm — and does not fail the
job: raising would re-run the whole sweep every poll cycle (1s) on a
release that keeps failing, the trap app.reconciliation_runner.run_all_daily
avoids the same way. The next night's run tries it again.
"""

import logging

from sqlalchemy.orm import Session

from app.inventory.services.reservation_sweep import release_orphaned_reservations

logger = logging.getLogger(__name__)


def run_daily_reservation_sweep(db: Session) -> None:
    result = release_orphaned_reservations(db)
    counts = {
        "released": len(result.released),
        "keptSigned": result.kept_signed,
        "keptRecent": result.kept_recent,
        "changed": result.changed,
        "failed": len(result.failed),
    }
    summary = (
        f"released={counts['released']} kept_signed={counts['keptSigned']} kept_recent={counts['keptRecent']} "
        f"changed={counts['changed']} failed={counts['failed']}"
    )
    if result.failed:
        failed_ids = [str(item_id) for item_id in result.failed]
        logger.error(
            "inventory.reservation_sweep: %s failed_stock_item_ids=%s",
            summary,
            ",".join(failed_ids),
            extra={**counts, "failedStockItemIds": failed_ids},
        )
        return
    logger.info("inventory.reservation_sweep: %s", summary, extra=counts)
