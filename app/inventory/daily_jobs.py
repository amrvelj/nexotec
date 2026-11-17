"""The daily jobs `app/worker.py` registers for inventory: the
orphan-reservation sweep (KAN-122, app.inventory.services.reservation_sweep)
and the repair of stock items left on a merged vehicle record (KAN-724,
app.inventory.services.vehicle_merge). A
context-level module, like app.integration.daily_jobs, so the worker never
reaches into this context's services.

One log line per run with its counts — the record Anto asked for
(2026-10-07). The counts are in the message text itself, so they read the
same in any log format; they are passed as `extra` too, which the worker's
JSON formatter (app.core.observability, since KAN-248) writes as fields.
A failed release is logged at ERROR — the alarm — and does not fail the
job: raising would re-run the whole sweep on every scheduler retry
(KAN-303 backoff) on a release that keeps failing, the trap app.reconciliation_runner.run_all_daily
avoids the same way. The next night's run tries it again.
"""

import logging

from sqlalchemy.orm import Session

from app.inventory.services.reservation_sweep import release_orphaned_reservations
from app.inventory.services.vehicle_merge import repair_items_left_on_merged_records

logger = logging.getLogger(__name__)


def run_daily_reservation_sweep(db: Session) -> None:
    result = release_orphaned_reservations(db)
    counts = {
        "released": len(result.released),
        "keptSigned": result.kept_signed,
        "keptRecent": result.kept_recent,
        "keptUnsynced": result.kept_unsynced,
        "changed": result.changed,
        "failed": len(result.failed),
    }
    summary = (
        f"released={counts['released']} kept_signed={counts['keptSigned']} kept_recent={counts['keptRecent']} "
        f"kept_unsynced={counts['keptUnsynced']} changed={counts['changed']} failed={counts['failed']}"
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


def run_daily_merged_record_repair(db: Session) -> None:
    """KAN-724: one log line per run with its counts, ERROR when a record's
    move failed (the alarm; the next night tries it again, as the sweep)."""

    result = repair_items_left_on_merged_records(db)
    counts = {
        "records": result.records,
        "moved": result.moved,
        "stillBlocked": result.still_blocked,
        "failed": len(result.failed),
    }
    summary = (
        f"records={counts['records']} moved={counts['moved']} still_blocked={counts['stillBlocked']} "
        f"failed={counts['failed']}"
    )
    if result.failed:
        failed_ids = [str(vehicle_id) for vehicle_id in result.failed]
        logger.error(
            "inventory.merged_record_items.repair: %s failed_vehicle_ids=%s",
            summary,
            ",".join(failed_ids),
            extra={**counts, "failedVehicleIds": failed_ids},
        )
        return
    logger.info("inventory.merged_record_items.repair: %s", summary, extra=counts)
