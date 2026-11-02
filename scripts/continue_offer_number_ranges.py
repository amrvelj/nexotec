"""KAN-207 — each Betrieb's `offer` number range takes over the offer sequence
Sales numbered with before it (Anto, 2026-11-01, option 1: "the range
continues the existing sequence"; the next offer is `O-000124` after
`O-000123`).

Why a script: the platform migration that creates the ranges may not read
Sales' table. It does not decide a number either way — from the first offer
on, `allocate_number` continues after Sales' old counter whatever this
script did (`continue_after`) — it only makes the Number ranges screen show
each Betrieb's real current value before its next offer. Run it once per
environment after the migration.

It reads Sales' last offer counter per Betrieb through `sales.public` and
sets the range's current value through `platform.public`. A range that has
handed out a number already, or stands at that value or beyond, is left
alone; the range stays unfrozen (Betriebe already active are seeded unfrozen,
so the manager can still change the pattern). Each change is audited
(`number_range_continued`). It publishes no event.

Dry run by default: the real path runs in one transaction that is rolled
back, and the report says what a `--commit` run would change. Idempotent.

Usage:
    python scripts/continue_offer_number_ranges.py            # dry run
    python scripts/continue_offer_number_ranges.py --commit
"""

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.platform.public import NumberRangeDocumentType, continue_sequence
from app.sales.public import legacy_offer_counters


@dataclass
class Report:
    continued: list[tuple[uuid.UUID, int]] = field(default_factory=list)
    unchanged: int = 0


def run(db: Session, *, commit: bool) -> Report:
    report = Report()
    for dealership_id, last in sorted(legacy_offer_counters(db).items()):
        if continue_sequence(db, dealership_id=dealership_id, document_type=NumberRangeDocumentType.OFFER, after=last):
            report.continued.append((dealership_id, last))
        else:
            report.unchanged += 1
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def main(argv: Sequence[str] | None = None, *, session_factory: Callable[[], Session] = SessionLocal) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args(argv)
    db = session_factory()
    try:
        report = run(db, commit=args.commit)
    finally:
        db.close()
    mode = "written" if args.commit else "DRY RUN — would write (rolled back; pass --commit)"
    print(f"offer number ranges: {mode}")
    for dealership_id, last in report.continued:
        print(f"  {dealership_id}: continues after O-{last:06d}")
    print(f"  continued: {len(report.continued)}, unchanged: {report.unchanged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
