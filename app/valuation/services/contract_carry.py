"""Which contracts carry a valuation as their trade-in — valuation's own
answer (KAN-631, ADR-131; the model's docstring has the states and why each
is written when it is).

Two layers. `mark_pending` / `mark_signed` / `mark_released` write the row
and flush, never commit: the backfill runs them in one transaction it rolls
back on a dry run. `record_*` are the calls Sales makes through
`valuation.public` (ADR-047 Pattern B): one transition, then their own
commit, on the session Sales opened for them.

A valuation that is not this tenant's is never recorded (ADR-029): Sales'
own reconciliation reports a contract naming another dealership's valuation
(KAN-259), and the reads below, like the Sales reads they replace, only ever
counted the valuation's own dealership.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from app.valuation.models.valuation import Valuation

logger = logging.getLogger(__name__)


def _is_tenants_valuation(db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID) -> bool:
    return (
        db.scalar(select(Valuation.id).where(Valuation.id == valuation_id, Valuation.tenant_id == tenant_id))
        is not None
    )


def _locked_row(db: Session, *, valuation_id: uuid.UUID, contract_id: uuid.UUID) -> ValuationContractCarry | None:
    return db.scalar(
        select(ValuationContractCarry)
        .where(ValuationContractCarry.valuation_id == valuation_id, ValuationContractCarry.contract_id == contract_id)
        .with_for_update()
    )


def _row_or_new(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str | None,
    state: ContractCarryState,
    actor_id: uuid.UUID | None,
) -> tuple[ValuationContractCarry, bool]:
    """The (valuation, contract) row, locked, and whether this call inserted
    it in `state`. Two first writers race on the unique constraint: the
    savepoint keeps the caller's transaction alive, and the locked re-read
    then waits for the winner's row (as `allocate_valuation_number` does)."""

    row = _locked_row(db, valuation_id=valuation_id, contract_id=contract_id)
    if row is not None:
        return row, False
    db.flush()
    try:
        with db.begin_nested():
            row = ValuationContractCarry(
                tenant_id=tenant_id,
                valuation_id=valuation_id,
                contract_id=contract_id,
                contract_label=contract_label,
                contract_label_refreshed_at=utcnow() if contract_label is not None else None,
                state=state,
                created_by=actor_id,
                updated_by=actor_id,
            )
            db.add(row)
        return row, True
    except IntegrityError:
        pass  # the concurrent writer's row now exists; re-read it below
    row = _locked_row(db, valuation_id=valuation_id, contract_id=contract_id)
    assert row is not None, "valuation_contract_carry row missing after its INSERT or the concurrent winner's"
    return row, False


def _refresh_label(row: ValuationContractCarry, contract_label: str | None) -> None:
    if contract_label is not None and row.contract_label != contract_label:
        row.contract_label = contract_label
        row.contract_label_refreshed_at = utcnow()


def mark_pending(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """A pending contract carries the valuation. True when this call made
    the row pending (it was absent or released), so a caller whose own write
    then fails knows the release is its to make. A signed row stays signed.
    Flushes; never commits."""

    if not _is_tenants_valuation(db, tenant_id=tenant_id, valuation_id=valuation_id):
        logger.warning(
            "valuation_contract_carry_skipped",
            extra={"valuationId": str(valuation_id), "contractId": str(contract_id), "reason": "not_this_tenants"},
        )
        return False
    row, inserted = _row_or_new(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        contract_label=contract_label, state=ContractCarryState.PENDING, actor_id=actor_id,
    )
    if inserted:
        db.flush()
        return True
    _refresh_label(row, contract_label)
    if row.state != ContractCarryState.RELEASED:
        db.flush()
        return False
    row.state = ContractCarryState.PENDING
    row.updated_by = actor_id
    db.flush()
    return True


def mark_signed(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    signed_at: dt.datetime,
    contract_label: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> None:
    """A signed contract carries the valuation — for good (ADR-066): a
    signed row keeps its first `signed_at`. Flushes; never commits."""

    if not _is_tenants_valuation(db, tenant_id=tenant_id, valuation_id=valuation_id):
        logger.warning(
            "valuation_contract_carry_skipped",
            extra={"valuationId": str(valuation_id), "contractId": str(contract_id), "reason": "not_this_tenants"},
        )
        return
    row, _inserted = _row_or_new(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        contract_label=contract_label, state=ContractCarryState.SIGNED, actor_id=actor_id,
    )
    _refresh_label(row, contract_label)
    if row.state != ContractCarryState.SIGNED or row.signed_at is None:
        row.state = ContractCarryState.SIGNED
        row.signed_at = row.signed_at or signed_at
        row.updated_by = actor_id
    db.flush()


def mark_released(
    db: Session,
    *,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """The contract no longer carries the valuation. Only a pending row is
    released: a signed one stays signed (ADR-066), and no row is no carry.
    True when this call released it. Flushes; never commits."""

    row = _locked_row(db, valuation_id=valuation_id, contract_id=contract_id)
    if row is None or row.state != ContractCarryState.PENDING:
        if row is not None and row.state == ContractCarryState.SIGNED:
            logger.warning(
                "valuation_contract_carry_release_refused",
                extra={"valuationId": str(valuation_id), "contractId": str(contract_id), "reason": "signed"},
            )
        return False
    row.state = ContractCarryState.RELEASED
    row.updated_by = actor_id
    db.flush()
    return True


def record_pending(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str | None,
    actor_id: uuid.UUID | None,
) -> bool:
    """`mark_pending`, then its own commit (ADR-047 Pattern B)."""

    made_pending = mark_pending(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        contract_label=contract_label, actor_id=actor_id,
    )
    db.commit()
    return made_pending


def record_signed(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    signed_at: dt.datetime,
    contract_label: str | None,
    actor_id: uuid.UUID | None,
) -> None:
    """`mark_signed`, then its own commit (ADR-047 Pattern B)."""

    mark_signed(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        signed_at=signed_at, contract_label=contract_label, actor_id=actor_id,
    )
    db.commit()


def record_released(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> bool:
    """`mark_released`, then its own commit (ADR-047 Pattern B). The tenant
    names whose row it may be: another dealership's is left alone."""

    released = False
    if _is_tenants_valuation(db, tenant_id=tenant_id, valuation_id=valuation_id):
        released = mark_released(db, valuation_id=valuation_id, contract_id=contract_id, actor_id=actor_id)
    db.commit()
    return released


def carry_states(
    db: Session, *, tenant_id: uuid.UUID, valuation_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, set[ContractCarryState]]:
    """For each of `valuation_ids` that a contract of this dealership
    carries, pending or signed, the states it is carried in — read in ONE
    statement, so the answer is one snapshot (KAN-520: a pending row only
    ever turns signed or released, never absent)."""

    if not valuation_ids:
        return {}
    rows = db.execute(
        select(ValuationContractCarry.valuation_id, ValuationContractCarry.state).where(
            ValuationContractCarry.tenant_id == tenant_id,
            ValuationContractCarry.valuation_id.in_(list(valuation_ids)),
            ValuationContractCarry.state.in_([ContractCarryState.PENDING, ContractCarryState.SIGNED]),
        )
    ).all()
    states: dict[uuid.UUID, set[ContractCarryState]] = {}
    for valuation_id, state in rows:
        states.setdefault(valuation_id, set()).add(state)
    return states


def valuations_carried_by_signed_contracts(
    db: Session, *, tenant_id: uuid.UUID, valuation_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The subset of `valuation_ids` a signed contract of this dealership
    carries — cancelled since included (ADR-066). KAN-115's question, now
    answered from valuation's own rows."""

    return {
        valuation_id
        for valuation_id, states in carry_states(db, tenant_id=tenant_id, valuation_ids=valuation_ids).items()
        if ContractCarryState.SIGNED in states
    }
