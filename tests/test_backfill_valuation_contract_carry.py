"""KAN-631 — `scripts/backfill_valuation_contract_carry.py` gives contracts
written before valuation kept its own carry rows the row Sales' calls would
have written: dry run by default (the real path, rolled back, commits
refused), idempotent, a signed row never taken back."""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.base import utcnow
from app.sales.models.contract import ContractStatus, SalesContract
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from scripts.backfill_valuation_contract_carry import backfill, render
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_trade_in_valuation_use import _valuation


def _legacy_contract(db_session, tenant_id, valuation_id, *, status, signed: bool) -> SalesContract:
    """A contract as written before KAN-631: valuation was never told."""

    contract = SalesContract(
        tenant_id=tenant_id, contract_number=f"C-{uuid.uuid4().hex[:6]}", status=status,
        trade_in_valuation_id=valuation_id, signed_at=utcnow() - dt.timedelta(days=9) if signed else None,
    )
    db_session.add(contract)
    db_session.commit()
    return contract


def _rows(db_session) -> dict[uuid.UUID, ContractCarryState]:
    db_session.expire_all()
    return {row.contract_id: row.state for row in db_session.scalars(select(ValuationContractCarry)).all()}


def test_the_backfill_dry_runs_then_writes_each_contracts_carry_once(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    pending = _legacy_contract(db_session, dealership.id, valuation.id, status=ContractStatus.PENDING, signed=False)
    signed = _legacy_contract(db_session, dealership.id, valuation.id, status=ContractStatus.CONFIRMED, signed=True)
    cancelled_signed = _legacy_contract(db_session, dealership.id, valuation.id, status=ContractStatus.CANCELLED, signed=True)
    _legacy_contract(db_session, dealership.id, valuation.id, status=ContractStatus.CANCELLED, signed=False)
    foreign = _legacy_contract(db_session, uuid.uuid4(), valuation.id, status=ContractStatus.CONFIRMED, signed=True)

    dry = backfill(db_session, commit=False)

    assert _rows(db_session) == {}
    assert dry.writes == 3 and dry.foreign == [(foreign.id, valuation.id)] and dry.conflicts == []
    assert "DRY RUN" in render(dry, committed=False)

    written = backfill(db_session, commit=True)

    assert written.writes == 3
    assert _rows(db_session) == {
        pending.id: ContractCarryState.PENDING,
        signed.id: ContractCarryState.SIGNED,
        cancelled_signed.id: ContractCarryState.SIGNED,
    }
    again = backfill(db_session, commit=True)
    assert again.writes == 0 and again.unchanged == 3


def test_a_replaced_valuations_pending_row_is_released_and_a_signed_row_is_never_taken_back(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    old, new = _valuation(db_session, dealership.id, group_id), _valuation(db_session, dealership.id, group_id)
    contract = _legacy_contract(db_session, dealership.id, new.id, status=ContractStatus.PENDING, signed=False)
    other = _legacy_contract(db_session, dealership.id, new.id, status=ContractStatus.PENDING, signed=False)
    db_session.add_all([
        ValuationContractCarry(tenant_id=dealership.id, valuation_id=old.id, contract_id=contract.id, state=ContractCarryState.PENDING),
        ValuationContractCarry(
            tenant_id=dealership.id, valuation_id=new.id, contract_id=other.id, state=ContractCarryState.SIGNED,
            signed_at=utcnow(),
        ),
    ])
    db_session.commit()

    report = backfill(db_session, commit=True)

    db_session.expire_all()
    states = {
        (row.valuation_id, row.contract_id): row.state for row in db_session.scalars(select(ValuationContractCarry)).all()
    }
    assert states == {
        (old.id, contract.id): ContractCarryState.RELEASED,
        (new.id, contract.id): ContractCarryState.PENDING,
        (new.id, other.id): ContractCarryState.SIGNED,
    }
    assert report.conflicts == [((new.id, other.id), "signed here, pending in Sales")]
