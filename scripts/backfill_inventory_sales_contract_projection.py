"""Fill Stock's projection of Sales' contracts for contracts older than its
consumers (KAN-630).

Since KAN-630 Stock's orphan sweep and «Einkauf stornieren» read Stock's own
projection (`inventory_sales_contract`), fed by `sales.contract.created`,
`.confirmed`, `.invoice_requested` and `.cancelled` from the deploy on. A
contract whose facts were delivered before that — every contract a
Betrieb has today, legacy sales migrated without events included — is not
in it. This script reads every contract through
`app.sales.public.list_contract_facts` (plain data, on a session of its
own, closed before anything is written) and merges each one into the
projection through `app.inventory.public.apply_backfilled_contract` — the
consumers' own merge, so a fact consumed meanwhile is never overwritten by
the older read — then records the run (`record_backfill`). Until a run is
recorded the sweep keeps every reservation of a contract the projection has
never heard of, and both rules count a published fact Stock never applied as
in flight; after it, they trust the projection for what came before.

Publishes no events: it copies facts Sales already published.

MANDATORY DRY RUN. The default writes nothing: the real merge runs in one
transaction that is rolled back (and any commit on the way is refused), so
the report says what --commit writes. Re-runnable: a second --commit run
changes no row and records one more run.

Run it once per environment after the deploy, once the old worker has
stopped (a worker without the new consumers could still mark a fact
delivered after this read). The nightly check «sales_contract missing from
inventory_sales_contract» names every contract it has not covered yet.

Exit status: 0 when the run is recorded (--commit) or nothing would change
(dry run), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_inventory_sales_contract_projection.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_inventory_sales_contract_projection.py --commit  # writes
"""

import argparse
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.db import SessionLocal
from app.inventory.public import apply_backfilled_contract, record_backfill
from app.sales.public import ContractFacts, list_contract_facts

_PAGE = 500


@dataclass
class Report:
    contracts_read: int = 0
    rows_written: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    invoice_requested: int = 0


def _read_all_contracts(session_factory: Callable[[], Session]) -> list[ContractFacts]:
    facts: list[ContractFacts] = []
    after: uuid.UUID | None = None
    with session_factory() as sales_db:
        while True:
            page = list_contract_facts(sales_db, after=after, limit=_PAGE)
            if not page:
                break
            facts.extend(page)
            after = page[-1].contract_id
    return facts


def _refuse_commit(session: Session) -> None:
    raise RuntimeError("dry run: a commit was attempted — refused, nothing is written")


def backfill(*, commit: bool, session_factory: Callable[[], Session] = SessionLocal) -> Report:
    report = Report()
    read_at = utcnow()
    facts = _read_all_contracts(session_factory)

    db = session_factory()
    if not commit:
        event.listen(db, "before_commit", _refuse_commit)
    try:
        for fact in facts:
            report.contracts_read += 1
            report.by_status[fact.status] += 1
            if fact.invoice_requested_at is not None:
                report.invoice_requested += 1
            if apply_backfilled_contract(
                db,
                tenant_id=fact.tenant_id,
                contract_id=fact.contract_id,
                status=fact.status,
                contract_number=fact.contract_number,
                stock_item_id=fact.stock_item_id,
                invoice_requested_at=fact.invoice_requested_at,
                read_at=read_at,
            ):
                report.rows_written += 1
        record_backfill(db, contracts_read=report.contracts_read, rows_written=report.rows_written)
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
        f"KAN-630 Stock's projection of Sales' contracts — backfill ({mode})",
        "",
        f"contracts read: {report.contracts_read}",
        *[f"  {status}: {count}" for status, count in sorted(report.by_status.items())],
        f"  with an invoice requested: {report.invoice_requested}",
        f"projection rows {'written' if committed else 'to write'} (new or changed): {report.rows_written}",
        f"run {'recorded' if committed else 'would be recorded'} in inventory_sales_contract_backfill",
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
    return 1 if report.rows_written else 0


if __name__ == "__main__":
    sys.exit(main())
