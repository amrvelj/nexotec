"""Reserve the manual-configuration pipeline items Stock created before
KAN-158, and record the cancellations it never consumed (KAN-166). The
rules are app.inventory.services.reservation_backfill's docstring.

MANDATORY DRY RUN. The default writes nothing: each contract's transaction
is rolled back, and the report says what a real run would do. Nothing
commits until a person has read that report and re-runs with --commit,
the same convention as migrate_transaction_rows.py.

Re-runnable: a second --commit run changes nothing and publishes nothing.

Exit status: 0 when nothing needs a person's attention, 1 otherwise — so
a run can gate a deploy step without parsing the report.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_manual_configuration_reservations.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_manual_configuration_reservations.py --commit  # writes
"""

import argparse
import sys

from app.db import SessionLocal
from app.inventory.services.reservation_backfill import (
    BackfillOutcome,
    BackfillReport,
    backfill_manual_configuration_reservations,
)


def render(report: BackfillReport) -> str:
    mode = "COMMITTED" if report.committed else "DRY RUN — nothing written"
    lines = [f"KAN-166 manual-configuration reservation backfill ({mode})", ""]
    for outcome, count in report.counts().items():
        lines.append(f"{outcome.value}: {count}")
    releasing = [
        line for line in report.lines if line.outcome == BackfillOutcome.CANCELLATION_RECORDED and line.detail
    ]
    lines += ["", f"RELEASES (inventory.stock_item.released published): {len(releasing)} contract(s)"]
    for line in releasing:
        lines.append(f"  contract {line.contract_id} ({line.contract_label}, tenant {line.tenant_id}) — {line.detail}")
    attention = report.needs_attention
    lines += ["", f"NEEDS ATTENTION: {len(attention)}"]
    for line in attention:
        lines.append(
            f"  contract {line.contract_id} (tenant {line.tenant_id}) stock item {line.stock_item_id}"
            f" — {line.outcome.value}{': ' + line.detail if line.detail else ''}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = backfill_manual_configuration_reservations(db, commit=args.commit)
    finally:
        db.close()

    print(render(report))
    return 1 if report.needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
