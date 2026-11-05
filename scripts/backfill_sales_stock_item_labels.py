"""Give existing stock-sourced offers and contracts their stock number (KAN-157).

CLAUDE.md rule 2: a cross-context reference carries a denormalised label
and when it was copied. `sales_offer.stock_item_id` and a stock-sourced
`sales_contract.stock_item_id` carried none until KAN-157; since then the
stock number is copied once, when the car is put on the offer or contract
(Anto, 2026-11-03, option A — a stock number never changes). This script
fills the rows written before: every offer and contract with a
`stock_item_id` and no `stock_item_label` takes its item's stock number,
read through `app.inventory.public.get_stock_numbers` (plain data, the
row's own tenant), stamped now. An id naming no stock item of the row's own
Betrieb is reported, never guessed — nightly reconciliation already reports
it as an orphan or a mismatch. Nothing is published: a label is a copy.

MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back, and the report says what a real run would
change. Re-run with --commit after reading it.

Re-runnable: a second --commit run finds nothing to fill.

Exit status: 0 when no row is left without a label it could have (in a dry
run: when none needed one), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_sales_stock_item_labels.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_sales_stock_item_labels.py --commit  # writes
"""

import argparse
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.db import SessionLocal
from app.inventory.public import get_stock_numbers
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer


@dataclass
class Report:
    filled: list[tuple[str, uuid.UUID, str]] = field(default_factory=list)
    unresolved: list[tuple[str, uuid.UUID, uuid.UUID]] = field(default_factory=list)


def _unlabelled(db: Session, model: type[SalesOffer] | type[SalesContract]) -> list:
    return list(
        db.scalars(
            select(model)
            .where(model.stock_item_id.is_not(None), model.stock_item_label.is_(None))
            .order_by(model.tenant_id, model.id)
        ).all()
    )


def backfill(db: Session, *, commit: bool) -> Report:
    report = Report()
    now = utcnow()
    for kind, model in (("offer", SalesOffer), ("contract", SalesContract)):
        rows = _unlabelled(db, model)
        by_tenant: dict[uuid.UUID, list] = defaultdict(list)
        for row in rows:
            by_tenant[row.tenant_id].append(row)
        for tenant_id, tenant_rows in by_tenant.items():
            numbers = get_stock_numbers(db, tenant_id=tenant_id, stock_item_ids={r.stock_item_id for r in tenant_rows})
            for row in tenant_rows:
                number = numbers.get(row.stock_item_id)
                if number is None:
                    report.unresolved.append((kind, row.id, row.stock_item_id))
                    continue
                # A label is a copy, refreshed on purpose: no version bump,
                # as the nightly label jobs (KAN-403, KAN-404).
                row.stock_item_label = number
                row.stock_item_denorm_refreshed_at = now
                report.filled.append((kind, row.id, number))
    db.flush()
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def render(report: Report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-157 Sales stock-number labels ({mode})", "", f"filled: {len(report.filled)}"]
    lines += [f"  {kind} {row_id}: {number}" for kind, row_id, number in report.filled]
    lines += ["", f"UNRESOLVED (no stock item of the row's own Betrieb): {len(report.unresolved)}"]
    lines += [f"  {kind} {row_id}: stock item {item_id}" for kind, row_id, item_id in report.unresolved]
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
    return 1 if report.filled or report.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
