"""Re-project the Sales grid rows written before KAN-408/KAN-409.

Before those tickets a deal row could disagree with its deal's live entity:
an offer edited after its contract existed wrote the offer's buyer, car and
document count onto the contract's row (KAN-408), and a cancelled contract
kept the row in its shape although its offer was free again (KAN-409) — so a
revived offer was missing from the grid, its seller's filter and the
unassigned queue, and an open offer cannot be edited to heal its row. The
rows to fix are exactly the ones the nightly checks report
(app.sales.reconciliation's deal-row StateChecks); each is re-projected
through the production writer, `upsert_deal_projection`, from its live
entity. Nothing else is written and nothing is published: the row is a
projection of facts already true.

MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back, and the report says what a real run would
change. Re-run with --commit after reading it, the convention of
backfill_manual_configuration_reservations.py.

Re-runnable: a second --commit run finds nothing to change.

Exit status: 0 when no row differs from its live entity afterwards (in a dry
run: when none needed a change), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_sales_deal_projection.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_sales_deal_projection.py --commit  # writes
"""

import argparse
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.reconciliation import find_rows_in_state
from app.db import SessionLocal
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.reconciliation import deal_row_checks
from app.sales.services.deal_projection import upsert_deal_projection


@dataclass(frozen=True)
class Reprojected:
    deal_id: uuid.UUID
    tenant_id: uuid.UUID
    before: str
    after: str


def _shape(deal: SalesDeal) -> str:
    return f"{deal.entity_type} {deal.number} ({deal.status})"


def _differing(db: Session) -> list[uuid.UUID]:
    return sorted({row for check in deal_row_checks() for row, _ in find_rows_in_state(db, check)})


def reproject(db: Session, *, commit: bool) -> tuple[list[Reprojected], list[uuid.UUID]]:
    """(rows re-projected, rows still differing afterwards)."""

    done = []
    for deal_id in _differing(db):
        deal = db.get(SalesDeal, deal_id)
        if deal is None:
            continue
        before = _shape(deal)
        if deal.contract_id is not None and (contract := db.get(SalesContract, deal.contract_id)) is not None:
            upsert_deal_projection(db, contract=contract)
        elif (offer := db.get(SalesOffer, deal.id)) is not None:
            upsert_deal_projection(db, offer=offer)
        else:
            continue
        done.append(Reprojected(deal_id=deal_id, tenant_id=deal.tenant_id, before=before, after=_shape(deal)))
    remaining = _differing(db)
    if commit:
        db.commit()
    else:
        db.rollback()
    return done, remaining


def render(done: list[Reprojected], remaining: list[uuid.UUID], *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-408/KAN-409 Sales deal-row re-projection ({mode})", "", f"re-projected: {len(done)}"]
    lines += [f"  deal {r.deal_id} (tenant {r.tenant_id}): {r.before} -> {r.after}" for r in done]
    lines += ["", f"STILL DIFFERING: {len(remaining)}"]
    lines += [f"  deal {deal_id}" for deal_id in remaining]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        done, remaining = reproject(db, commit=args.commit)
    finally:
        db.close()

    print(render(done, remaining, committed=args.commit))
    if args.commit:
        return 1 if remaining else 0
    return 1 if done or remaining else 0


if __name__ == "__main__":
    sys.exit(main())
