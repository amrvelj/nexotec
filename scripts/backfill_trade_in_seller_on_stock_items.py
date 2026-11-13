"""Give trade-in stock items created before KAN-632 their seller (KAN-674, step 1).

Since KAN-632 a trade-in's pipeline item keeps who traded the car in and when
the contract was signed (`trade_in_customer_id`, `trade_in_signed_at`,
inventory migration `21c14ae9dd31`), and its purchase and storno events carry
them to the customer context. An item created before KAN-632 keeps neither,
so `app/worker.py` still hands the customer consumers a fallback that asks
Sales (`sales.public.get_trade_in_seller`) for an event without the seller.

This script fills both columns of every trade-in item (`pipeline_ref`
`contract:<id>:trade_in`) that lacks either, from that same read — the
contract's customer, and its signature (else its creation), exactly what the
fallback answers — so the events those items publish from now on carry the
seller. A contract this item's Betrieb does not have, or one that names no
customer, is reported, never guessed. Nothing is published and no version is
bumped: the columns are Stock's copy of Sales' fact, read by no screen. Only
inventory rows are written (CLAUDE.md rule 12: the Sales side is a read).

Step 2 of KAN-674 — removing the worker's fallback — waits until this has run
in every environment and no pre-KAN-632 purchase or storno event is pending.

MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back, and the report says what a real run would
change. Re-run with --commit after reading it.

Items are read FOR NO KEY UPDATE SKIP LOCKED and held until the commit, so a
purchase booking writing an item meanwhile is never overwritten; an item
another writer holds at that moment is left for the next run and counted as
SKIPPED.

Re-runnable: a second --commit run finds nothing to fill.

Exit status: 0 when no trade-in item is left without a seller it could have —
none unresolved, none skipped (in a dry run: when none needed one), 1
otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_trade_in_seller_on_stock_items.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_trade_in_seller_on_stock_items.py --commit  # writes
"""

import argparse
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.inventory.models.stock_item import StockItem
from app.inventory.services.stock_item import pipeline_origin
from app.sales.public import get_trade_in_seller

_TRADE_IN_REF = "contract:%:trade_in"


@dataclass
class Report:
    # (stock item, contract, customer)
    filled: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = field(default_factory=list)
    # (stock item, contract or None, why)
    unresolved: list[tuple[uuid.UUID, uuid.UUID | None, str]] = field(default_factory=list)
    skipped: int = 0


def _missing_seller():
    return (
        StockItem.pipeline_ref.like(_TRADE_IN_REF),
        or_(StockItem.trade_in_customer_id.is_(None), StockItem.trade_in_signed_at.is_(None)),
    )


def _refuse_commit(session: Session) -> None:
    raise RuntimeError("dry run: a commit was attempted — refused, nothing is written")


def backfill(db: Session, *, commit: bool) -> Report:
    """Fills what it can; commits with `commit`, else rolls the same writes
    back, refusing any commit made while it runs (KAN-263)."""

    if not commit:
        event.listen(db, "before_commit", _refuse_commit)
    try:
        return _backfill(db, commit=commit)
    finally:
        if not commit:
            event.remove(db, "before_commit", _refuse_commit)


def _backfill(db: Session, *, commit: bool) -> Report:
    report = Report()
    items = list(
        db.scalars(
            select(StockItem)
            .where(*_missing_seller())
            .order_by(StockItem.tenant_id, StockItem.id)
            .with_for_update(key_share=True, skip_locked=True)
        ).all()
    )
    waiting = db.scalar(select(func.count()).select_from(StockItem).where(*_missing_seller())) or 0
    report.skipped = max(waiting - len(items), 0)
    for item in items:
        origin = pipeline_origin(item.pipeline_ref)
        if origin.get("originRole") != "trade_in":
            report.unresolved.append((item.id, None, "pipeline_ref names no contract"))
            continue
        contract_id = uuid.UUID(origin["originContractId"])
        seller = get_trade_in_seller(db, tenant_id=item.tenant_id, contract_id=contract_id)
        if seller is None:
            report.unresolved.append((item.id, contract_id, "no contract of this Betrieb naming a customer"))
            continue
        item.trade_in_customer_id = seller.customer_id
        item.trade_in_signed_at = seller.known_since
        report.filled.append((item.id, contract_id, seller.customer_id))
    db.flush()
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def render(report: Report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-674 trade-in sellers on stock items ({mode})", "", f"filled: {len(report.filled)}"]
    lines += [f"  stock item {item}: contract {contract} -> customer {customer}" for item, contract, customer in report.filled]
    lines += ["", f"UNRESOLVED: {len(report.unresolved)}"]
    lines += [f"  stock item {item}: contract {contract}: {why}" for item, contract, why in report.unresolved]
    lines += ["", f"SKIPPED (held by another writer — run again): {report.skipped}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        report = backfill(db, commit=args.commit)
    print(render(report, committed=args.commit))
    left = report.unresolved or report.skipped or (not args.commit and report.filled)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
