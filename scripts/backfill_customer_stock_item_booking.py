"""Re-seed the customer context's record of Stock's bookings from the event
log (KAN-632, ADR-147).

`customer_stock_item_booking` holds one row per `inventory.stock_item.purchased`
and `inventory.stock_item.storno` event; the trade-in consumers judge from it
whether the event in front of them was overtaken. The customer migration
`c4e8a1f7d932` seeded it once from `outbox_message`, and from the deploy on
the consumers `customer.stock_item_bookings_on_purchased` / `_on_storno`
write each new event's row. Between the two, during a rolling deploy, a
worker still running the old code may consume such an event: it records the
event as processed under the trade-in consumers' names and writes no row of
the new consumers' — and since those are new consumer names the new worker
delivers the event again to them only if it is still pending. An event the
old worker finished is never delivered again, so its row is missing and the
ordering guard is blind to it.

This script runs the migration's seed again as an idempotent re-seed
(`INSERT … SELECT … ON CONFLICT (event_id) DO NOTHING`,
`app.customer.services.stock_item_booking.reseed_bookings_from_outbox`):
every booking event of a Betrieb without its row gets one, a row already
there is left alone. Publishes no events: it copies facts Stock already
published.

MANDATORY DRY RUN. The default writes nothing: the real insert runs in one
transaction that is rolled back (and any commit on the way is refused), so
the report says what --commit adds. Re-runnable: a second --commit run adds
no row.

Run it once per environment after the deploy, once the old worker has
stopped (until then it could consume another event without writing its
row). The nightly check «stock item booking event with no
customer_stock_item_booking row» names every event still missing one.

Exit status: 0 after --commit, or when a dry run would add nothing; 1 when a
dry run would add rows.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_customer_stock_item_booking.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_customer_stock_item_booking.py --commit  # writes
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.customer.models.stock_item_booking import StockItemBooking
from app.customer.services.stock_item_booking import reseed_bookings_from_outbox
from app.db import SessionLocal


@dataclass
class Report:
    rows_before: int = 0
    rows_added: int = 0


def _refuse_commit(session: Session) -> None:
    raise RuntimeError("dry run: a commit was attempted — refused, nothing is written")


def backfill(*, commit: bool, session_factory: Callable[[], Session] = SessionLocal) -> Report:
    report = Report()
    db = session_factory()
    if not commit:
        event.listen(db, "before_commit", _refuse_commit)
    try:
        report.rows_before = db.scalar(select(func.count()).select_from(StockItemBooking)) or 0
        report.rows_added = reseed_bookings_from_outbox(db)
        if commit:
            db.commit()
        else:
            db.rollback()
    finally:
        if not commit:
            event.remove(db, "before_commit", _refuse_commit)
        db.close()
    return report


def render(report: Report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [
        f"KAN-632 customer_stock_item_booking — re-seed from the event log ({mode})",
        "",
        f"booking rows before: {report.rows_before}",
        f"booking rows {'added' if committed else 'to add'} (events without a row): {report.rows_added}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)
    report = backfill(commit=args.commit)
    print(render(report, committed=args.commit))
    if args.commit:
        return 0
    return 1 if report.rows_added else 0


if __name__ == "__main__":
    sys.exit(main())
