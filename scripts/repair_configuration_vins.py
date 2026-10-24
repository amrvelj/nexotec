"""Repair the configuration VINs stored before KAN-338 that no save can pass
(KAN-395; Anto, 2026-10-20, option B: clean the data once). The rules are
app.vehicle.services.configuration.repair_stored_vins's docstring: a VIN that
normalises to a valid VIN is normalised, one that does not is cleared, a
configuration first registered before 1981 is left alone, and every change
is audited with the old value.

MANDATORY DRY RUN. The default writes nothing: the repair runs in one
transaction that is rolled back, and the report lists what a real run would
change. Re-run with --commit after reading it, the convention of
backfill_manual_configuration_reservations.py.

Re-runnable: a second --commit run changes nothing.

Exit status: 0 when no configuration needed a change, 1 otherwise — the
dry run that lists rows is a report a person must read.

Usage:
    DMS_DATABASE_URL=... python scripts/repair_configuration_vins.py           # dry run
    DMS_DATABASE_URL=... python scripts/repair_configuration_vins.py --commit  # writes
"""

import argparse
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.vehicle.services.configuration import VinRepair, repair_stored_vins


def run(db: Session, *, commit: bool) -> list[VinRepair]:
    repairs = repair_stored_vins(db)
    if commit:
        db.commit()
    else:
        db.rollback()
    return repairs


def render(repairs: list[VinRepair], *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    normalised = [r for r in repairs if r.after is not None]
    cleared = [r for r in repairs if r.after is None]
    lines = [
        f"KAN-395 configuration VIN repair ({mode})",
        "",
        f"normalised: {len(normalised)}",
        f"cleared: {len(cleared)}",
    ]
    for r in repairs:
        lines.append(f"  configuration {r.configuration_id} (tenant {r.tenant_id}): {r.before!r} -> {r.after!r}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        repairs = run(db, commit=args.commit)
    finally:
        db.close()

    print(render(repairs, committed=args.commit))
    return 1 if repairs else 0


if __name__ == "__main__":
    sys.exit(main())
