"""Fill Vehicle's projection of the records a Betrieb holds in stock for the
cars already in stock (KAN-667).

Since KAN-667 the identity PATCH (`PATCH /v1/vehicle-mdm/{id}`) refuses a VIN
change on a car the caller's Betrieb holds in stock, and the Vehicle 360 links
it to «VIN korrigieren». Both read Vehicle's own projection
(`vehicle_stock_holding`), fed by `inventory.pipeline_vehicle.vin_assigned`
and `inventory.stock_item.sold` from the deploy on. A stock item that got its
record before that is not in it. This script reads every stock item that
holds a record and has not left stock through
`app.inventory.public.list_held_vehicle_records` (plain data, on a session of
its own, closed before anything is written) and merges each one into the
projection through `app.vehicle.public.apply_backfilled_holding` — the
consumers' own merge, so a fact consumed meanwhile (a sale) is never undone
by the older read.

Seeded from Stock's current state (as KAN-630 seeds from Sales' contracts),
not by replaying the outbox: an item's record is set once and kept until it
leaves stock, so what Stock holds now is the whole answer — and it does not
depend on which `vin_assigned` rows the outbox still holds, nor on a stock
number those published before KAN-667 do not carry.

Publishes no events: it copies what Stock already holds.

MANDATORY DRY RUN. The default writes nothing: the real merge runs in one
transaction that is rolled back (and any commit on the way is refused), so
the report says what --commit writes. Re-runnable: a second --commit run
changes no row.

Run it once per environment after the deploy, once the old worker has
stopped (a worker without the new consumers could still mark a
`vin_assigned` delivered after this read). The nightly check «stock item
holding a record missing from vehicle_stock_holding» names every car it has
not covered yet.

Exit status: 0 on --commit, or on a dry run with nothing to write; 1 when a
dry run would write.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_vehicle_stock_holding.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_vehicle_stock_holding.py --commit  # writes
"""

import argparse
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.db import SessionLocal
from app.inventory.public import HeldVehicleRecord, list_held_vehicle_records
from app.vehicle.public import apply_backfilled_holding

_PAGE = 500


@dataclass
class Report:
    items_read: int = 0
    rows_written: int = 0


def _read_all_items(session_factory: Callable[[], Session]) -> list[HeldVehicleRecord]:
    items: list[HeldVehicleRecord] = []
    after: uuid.UUID | None = None
    with session_factory() as inventory_db:
        while True:
            page = list_held_vehicle_records(inventory_db, after=after, limit=_PAGE)
            if not page:
                break
            items.extend(page)
            after = page[-1].stock_item_id
    return items


def _refuse_commit(session: Session) -> None:
    raise RuntimeError("dry run: a commit was attempted — refused, nothing is written")


def backfill(*, commit: bool, session_factory: Callable[[], Session] = SessionLocal) -> Report:
    report = Report()
    read_at = utcnow()
    items = _read_all_items(session_factory)

    db = session_factory()
    if not commit:
        event.listen(db, "before_commit", _refuse_commit)
    try:
        for item in items:
            report.items_read += 1
            if apply_backfilled_holding(
                db,
                tenant_id=item.tenant_id,
                stock_item_id=item.stock_item_id,
                stock_number=item.stock_number,
                vehicle_id=item.vehicle_id,
                read_at=read_at,
            ):
                report.rows_written += 1
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
    return "\n".join(
        [
            f"KAN-667 Vehicle's projection of the records a Betrieb holds in stock — backfill ({mode})",
            "",
            f"stock items holding a record read: {report.items_read}",
            f"projection rows {'written' if committed else 'to write'} (new or changed): {report.rows_written}",
        ]
    )


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
    return 1 if report.rows_written else 0


if __name__ == "__main__":
    sys.exit(main())
