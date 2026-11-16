"""Give existing located stock items their location's name (KAN-739).

CLAUDE.md rule 2: a cross-context reference carries a denormalised label and
when it was copied. `stock_item.location_id` (platform's Location.id) carried
none until KAN-739; since then the write path copies the name whenever the id
is set and the nightly `inventory.location_labels` keeps it current. This
script fills the rows written before — it is that same refresh
(`app.inventory.public.refresh_stock_location_labels`, the production
computation, reading names through `app.platform.public`) run once: every
item with a location id takes its location's name, stamped now; a current
name is only restamped. An id naming no location of the item's own Betrieb
is reported, never guessed (nightly reconciliation already reports one
naming nothing). Nothing is published: a label is a copy. No `version` bump.

MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back (batches are flushed, never committed), and
the report says what a real run would change. Re-run with --commit after
reading it.

Rows are read FOR NO KEY UPDATE SKIP LOCKED, so an item another writer holds
at that moment is left for the next run (or the next night's job).

Re-runnable: a second --commit run changes nothing.

Exit status: 0 when nothing is left to fill or unresolved (in a dry run:
when nothing needed filling), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_stock_item_location_labels.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_stock_item_location_labels.py --commit  # writes
"""

import argparse
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.inventory.public import refresh_stock_location_labels


def backfill(db: Session, *, commit: bool):
    report = refresh_stock_location_labels(db, commit=db.commit if commit else db.flush)
    if not commit:
        db.rollback()
    return report


def render(report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-739 stock item location labels ({mode})", "", f"filled or renamed: {len(report.changed)}"]
    lines += [f"  stock item {item_id}: {before!r} -> {after!r}" for item_id, before, after in report.changed]
    lines += ["", f"already current (restamped): {report.restamped}"]
    lines += ["", f"UNRESOLVED (no location of the item's own Betrieb): {len(report.unresolved)}"]
    lines += [f"  stock item {item_id}: location {location_id}" for item_id, location_id in report.unresolved]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = backfill(db, commit=args.commit)
    finally:
        db.close()

    print(render(report, committed=args.commit))
    if args.commit:
        return 1 if report.unresolved else 0
    return 1 if report.changed or report.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
