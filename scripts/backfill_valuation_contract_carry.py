"""Fill valuation's own «carried by a pending / signed contract» rows for the
contracts written before KAN-631 (ADR-131).

Since KAN-631 valuation answers «is this valuation carried by a pending or a
signed contract» — the `hasSignedContract` marker on the valuation list and
detail, «Als verwendet markieren», «Verwendung aufheben» — from its own
`valuation_contract_carry` rows, which Sales' calls write from then on. A
contract written before the deploy has none, so its valuation would read as
carried by nothing. This script gives each such contract its row, from
Sales' answer read through `app.sales.public.trade_in_valuation_carries`
(plain data), written through valuation's own transitions
(`app.valuation.services.contract_carry.mark_pending` / `mark_signed` /
`mark_released` — the same ones Sales' calls make):

- a pending contract carrying a valuation → `pending`;
- a signed contract (`signed_at` set, cancelled since included, ADR-066) →
  `signed`;
- a contract cancelled unsigned → an existing pending row `released`, no
  new row;
- a pending row whose contract no longer carries its valuation (replaced
  before the deploy) → `released` — once it is older than `PENDING_GRACE`
  (one hour, the nightly check's): Sales' call writes a pending row before
  the Sales commit that makes the contract carry it, so a younger one may be
  a write in flight. It is reported as RECENT and left for a re-run.

Valuation's rows are read first, then Sales' answer on a session of its own
that ends before any row is written (ADR-047: no transaction spans both).

A signed row is never taken back (ADR-066): one Sales does not hold as
signed is reported as a CONFLICT and left for a person, as the nightly check
reports it. A contract naming another dealership's valuation is reported as
FOREIGN and gets no row (ADR-029; Sales' own check reports it, KAN-259).

Run it after the deploy, so every contract an old instance wrote is in the
read. MANDATORY DRY RUN. The default writes nothing: the real path runs in one
transaction that is rolled back, and any commit on the way is refused.
Re-run with --commit after reading it. Re-runnable: a second --commit run
writes nothing.

Exit status: 0 when nothing is in conflict, foreign or recent (dry run: and
nothing needed writing), 1 otherwise.

Usage:
    DMS_DATABASE_URL=... python scripts/backfill_valuation_contract_carry.py           # dry run
    DMS_DATABASE_URL=... python scripts/backfill_valuation_contract_carry.py --commit  # writes
"""

import argparse
import datetime as dt
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import utcnow
from app.db import SessionLocal
from app.sales.public import ContractValuationCarry, trade_in_valuation_carries
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from app.valuation.models.valuation import Valuation
from app.valuation.services.contract_carry import mark_pending, mark_released, mark_signed

_Key = tuple[uuid.UUID, uuid.UUID]  # (valuation_id, contract_id)

# Sales' call writes a pending row before the Sales commit that makes the
# contract carry it (`commit_carrying`); a pending row touched this recently
# may be such a write in flight. The nightly check's grace (`_CARRY_GRACE`).
PENDING_GRACE = dt.timedelta(hours=1)


@dataclass
class Report:
    written: dict[str, list[_Key]] = field(default_factory=dict)  # "absent->pending" etc.
    unchanged: int = 0
    conflicts: list[tuple[_Key, str]] = field(default_factory=list)
    foreign: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)  # (contract, valuation)
    recent_pending: list[_Key] = field(default_factory=list)  # stale-looking, inside the grace

    @property
    def writes(self) -> int:
        return sum(len(keys) for keys in self.written.values())


class _CommitRefused(RuntimeError):
    pass


def _rows(db: Session) -> dict[_Key, tuple[ContractCarryState, dt.datetime]]:
    rows = db.execute(
        select(
            ValuationContractCarry.valuation_id,
            ValuationContractCarry.contract_id,
            ValuationContractCarry.state,
            ValuationContractCarry.updated_at,
        )
    ).all()
    return {(valuation_id, contract_id): (state, updated_at) for valuation_id, contract_id, state, updated_at in rows}


def _states(db: Session) -> dict[_Key, ContractCarryState]:
    return {key: state for key, (state, _updated_at) in _rows(db).items()}


def _is_tenants(db: Session, carry: ContractValuationCarry) -> bool:
    return db.scalar(
        select(Valuation.id).where(Valuation.id == carry.valuation_id, Valuation.tenant_id == carry.tenant_id)
    ) is not None


def _apply(db: Session, carry: ContractValuationCarry, before: ContractCarryState | None, report: Report) -> None:
    assert carry.valuation_id is not None
    key = (carry.valuation_id, carry.contract_id)
    ids = {"valuation_id": carry.valuation_id, "contract_id": carry.contract_id}
    if carry.carry == "signed":
        assert carry.signed_at is not None
        mark_signed(db, tenant_id=carry.tenant_id, **ids, signed_at=carry.signed_at, contract_label=carry.contract_number)
    elif carry.carry == "pending":
        if before == ContractCarryState.SIGNED:
            report.conflicts.append((key, "signed here, pending in Sales"))
            return
        mark_pending(db, tenant_id=carry.tenant_id, **ids, contract_label=carry.contract_number)
    else:  # released: cancelled unsigned
        if before == ContractCarryState.SIGNED:
            report.conflicts.append((key, "signed here, cancelled unsigned in Sales"))
            return
        if before == ContractCarryState.PENDING:
            mark_released(db, **ids)


def _read_sales(
    session_factory: Callable[[], Session], *, stale_contract_ids: Callable[[list[ContractValuationCarry]], set[uuid.UUID]]
) -> tuple[list[ContractValuationCarry], dict[uuid.UUID, ContractValuationCarry]]:
    """Sales' side, on a session of its own that ends before valuation's rows
    are written (ADR-047: each context's work in its own transaction, as
    scripts/backfill_inventory_sales_contract_projection.py reads Sales):
    every contract carrying a valuation, then the current state of the
    contracts `stale_contract_ids` picks from that list."""

    with session_factory() as sales_db:
        carries = trade_in_valuation_carries(sales_db)
        stale = stale_contract_ids(carries)
        current = {c.contract_id: c for c in trade_in_valuation_carries(sales_db, contract_ids=stale)} if stale else {}
    return carries, current


def backfill(
    db: Session,
    *,
    commit: bool,
    session_factory: Callable[[], Session] | None = None,
    as_of: dt.datetime | None = None,
) -> Report:
    """`db` writes valuation's rows; Sales is read on sessions from
    `session_factory` (default: sessions on `db`'s engine)."""

    report = Report()
    as_of = as_of or utcnow()
    factory = session_factory or sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)

    def refuse(session: Session) -> None:
        # A savepoint's release (the insert race guard) fires before_commit
        # too; it keeps nothing past the transaction the dry run rolls back.
        if session.in_nested_transaction():
            return
        raise _CommitRefused("the dry run writes nothing: a commit was attempted")

    if not commit:
        event.listen(db, "before_commit", refuse)
    try:
        # Valuation's rows first, Sales after: a carry Sales' call wrote before
        # its own commit is then either already in Sales' answer or — pending,
        # touched within the grace — left alone below.
        rows = _rows(db)
        before = {key: state for key, (state, _updated_at) in rows.items()}

        def stale_contract_ids(carries: list[ContractValuationCarry]) -> set[uuid.UUID]:
            carried = {(c.valuation_id, c.contract_id) for c in carries}
            return {key[1] for key, state in before.items() if key not in carried and state != ContractCarryState.RELEASED}

        sales, current = _read_sales(factory, stale_contract_ids=stale_contract_ids)
        seen: set[_Key] = set()
        for carry in sales:
            assert carry.valuation_id is not None
            if not _is_tenants(db, carry):
                report.foreign.append((carry.contract_id, carry.valuation_id))
                continue
            key = (carry.valuation_id, carry.contract_id)
            seen.add(key)
            _apply(db, carry, before.get(key), report)
        # Rows whose contract no longer carries their valuation (or is gone).
        stale = {key: rows[key] for key, state in before.items() if key not in seen and state != ContractCarryState.RELEASED}
        for (valuation_id, contract_id), (state, updated_at) in sorted(stale.items()):
            contract = current.get(contract_id)
            if contract is not None and contract.valuation_id == valuation_id:
                continue  # foreign, reported above
            if state == ContractCarryState.SIGNED:
                report.conflicts.append(((valuation_id, contract_id), "signed here, not carried in Sales"))
                continue
            if as_of - updated_at < PENDING_GRACE:
                # `commit_carrying` writes the pending row before the Sales
                # commit that makes the contract carry it: Sales may not show
                # it yet. Released only once it is older than the grace.
                report.recent_pending.append((valuation_id, contract_id))
                continue
            mark_released(db, valuation_id=valuation_id, contract_id=contract_id)
        db.flush()
        after = _states(db)
        for key in sorted(set(before) | set(after)):
            was, now = before.get(key), after.get(key)
            if was == now:
                report.unchanged += 1
            else:
                label = f"{was.value if was else 'absent'}->{now.value if now else 'absent'}"
                report.written.setdefault(label, []).append(key)
        if commit:
            db.commit()
        else:
            db.rollback()
    finally:
        if not commit:
            event.remove(db, "before_commit", refuse)
    return report


def render(report: Report, *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [f"KAN-631 valuation_contract_carry backfill ({mode})", "", f"written: {report.writes}"]
    for label, keys in sorted(report.written.items()):
        lines.append(f"  {label}: {len(keys)}")
        lines += [f"    valuation {valuation_id} / contract {contract_id}" for valuation_id, contract_id in keys]
    lines += ["", f"unchanged: {report.unchanged}"]
    lines += ["", f"CONFLICT (a signed row is never taken back — for a person): {len(report.conflicts)}"]
    lines += [f"  valuation {v} / contract {c}: {why}" for (v, c), why in report.conflicts]
    lines += ["", f"FOREIGN (contract names another dealership's valuation — Sales' KAN-259 finding): {len(report.foreign)}"]
    lines += [f"  contract {c}: valuation {v}" for c, v in report.foreign]
    lines += ["", f"RECENT (pending, not carried in Sales, younger than {PENDING_GRACE} — re-run later): {len(report.recent_pending)}"]
    lines += [f"  valuation {v} / contract {c}" for v, c in report.recent_pending]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        report = backfill(db, commit=args.commit, session_factory=SessionLocal)
    finally:
        db.close()
    print(render(report, committed=args.commit))
    if args.commit:
        return 1 if report.conflicts or report.foreign or report.recent_pending else 0
    return 1 if report.writes or report.conflicts or report.foreign or report.recent_pending else 0


if __name__ == "__main__":
    sys.exit(main())
