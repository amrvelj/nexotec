"""Flag open deals whose stock car's price moved before KAN-553 (ADR-115).

Since KAN-553 Stock publishes `inventory.stock_item.commercial_changed` and
Sales flags every draft or open offer and pending contract on the car
(«Fahrzeug im Lager geändert»). A price changed before the deploy published
nothing, so those deals carry a frozen price the car no longer has and no
flag — the nightly check `… on a stock car whose price changed after the
freeze, without the vehicle-changed flag` reports them, and the seller has
no banner to refresh or dismiss. This script gives each such deal the flag,
through the consumer's own write (`vehicle_change.flag_deal`: merged fields,
audit row reason `backfill:KAN-553`, deal row re-projected): the frozen
`listPrice`/`effectivePrice` compared with the car's current ones, read
through `app.inventory.public.get_stock_item_pricing` (plain data, the
deal's own Betrieb). A deal already flagged or dismissed is left alone. A
stock item that no longer resolves is reported, never guessed.

MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back. Re-run with --commit after reading it.
Rows are read FOR NO KEY UPDATE SKIP LOCKED (offers, then contracts — the
Sales lock order); a held row is left for the next run, counted as SKIPPED.
Re-runnable: a second --commit run finds nothing to flag.

Exit status: 0 when nothing is left to flag, unresolved or skipped (dry run:
when nothing needed a flag), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_vehicle_changed_flags.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_vehicle_changed_flags.py --commit  # writes
"""

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.db import SessionLocal
from app.inventory.public import get_stock_item_pricing
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.vehicle_change import flag_deal

REASON = "backfill:KAN-553"


@dataclass
class Report:
    flagged: list[tuple[str, uuid.UUID, list[str]]] = field(default_factory=list)
    unresolved: list[tuple[str, uuid.UUID, uuid.UUID]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


def _money(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def _candidates(model: Any) -> ColumnElement[bool]:
    is_open = (
        SalesOffer.status.in_((OfferStatus.DRAFT, OfferStatus.OPEN))
        if model is SalesOffer
        else SalesContract.status == ContractStatus.PENDING
    )
    return (
        is_open
        & (model.vehicle_source == "stock")
        & model.stock_item_id.is_not(None)
        & model.vehicle_snapshot_frozen_at.is_not(None)
        & model.vehicle_changed_at.is_(None)
        & model.vehicle_change_dismissed_at.is_(None)
    )


def backfill(db: Session, *, commit: bool) -> Report:
    report = Report()
    now = utcnow()
    models: tuple[tuple[str, Any], ...] = (("offer", SalesOffer), ("contract", SalesContract))
    for kind, model in models:
        rows: list[SalesOffer | SalesContract] = list(
            db.scalars(
                select(model)
                .where(_candidates(model))
                .order_by(model.id)
                .with_for_update(key_share=True, skip_locked=True)
            ).all()
        )
        waiting = db.scalar(select(func.count()).select_from(model).where(_candidates(model))) or 0
        if waiting > len(rows):
            report.skipped[kind] = waiting - len(rows)
        for deal in rows:
            assert deal.stock_item_id is not None
            try:
                current = get_stock_item_pricing(db, tenant_id=deal.tenant_id, stock_item_id=deal.stock_item_id)
            except NotFoundError:
                report.unresolved.append((kind, deal.id, deal.stock_item_id))
                continue
            frozen = deal.vehicle_snapshot or {}
            fields = [
                name
                for name in ("listPrice", "effectivePrice")
                if _money(frozen.get(name)) != _money(current[name])
            ]
            if fields and flag_deal(db, deal=deal, changed_fields=fields, changed_at=now, reason=REASON):
                report.flagged.append((kind, deal.id, fields))
    db.flush()
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def render(report: Report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-553 «Fahrzeug im Lager geändert» backfill ({mode})", "", f"flagged: {len(report.flagged)}"]
    lines += [f"  {kind} {row_id}: {', '.join(fields)}" for kind, row_id, fields in report.flagged]
    lines += ["", f"UNRESOLVED (no stock item of the deal's own Betrieb): {len(report.unresolved)}"]
    lines += [f"  {kind} {row_id}: stock item {item_id}" for kind, row_id, item_id in report.unresolved]
    lines += ["", f"SKIPPED (held by another writer — run again): {sum(report.skipped.values())}"]
    lines += [f"  {kind}: {count}" for kind, count in sorted(report.skipped.items())]
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
        return 1 if report.unresolved or report.skipped else 0
    return 1 if report.flagged or report.unresolved or report.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
