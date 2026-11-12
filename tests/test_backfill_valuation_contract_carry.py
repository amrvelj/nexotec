"""KAN-631 — `scripts/backfill_valuation_contract_carry.py` gives contracts
written before valuation kept its own carry rows the row Sales' calls would
have written: dry run by default (the real path, rolled back, commits
refused), idempotent, a signed row never taken back."""

import datetime as dt
import uuid

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.base import utcnow
from app.sales.models.contract import ContractStatus, SalesContract
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from scripts.backfill_valuation_contract_carry import PENDING_GRACE, backfill, render
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


def _pending_row(db_session, tenant_id, valuation_id, contract_id, *, age: dt.timedelta) -> None:
    db_session.add(
        ValuationContractCarry(
            tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id, state=ContractCarryState.PENDING,
        )
    )
    db_session.commit()
    db_session.execute(
        update(ValuationContractCarry)
        .where(ValuationContractCarry.contract_id == contract_id)
        .values(updated_at=utcnow() - age)
    )
    db_session.commit()


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
    # Written before the deploy: older than the grace (KAN-631 review).
    _pending_row(db_session, dealership.id, old.id, contract.id, age=PENDING_GRACE + dt.timedelta(days=1))
    db_session.add_all([
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


def test_a_pending_row_whose_sales_write_may_still_be_in_flight_is_not_released(db_session):
    """KAN-631 review: `commit_carrying` writes the pending row before the
    Sales commit that makes the contract carry it. A stale-looking pending row
    touched within the grace may be that write in flight: it is reported as
    recent and kept; one older than the grace is released."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    old, new = _valuation(db_session, dealership.id, group_id), _valuation(db_session, dealership.id, group_id)
    # The Sales commit has not landed yet: the contract still carries `old`.
    in_flight = _legacy_contract(db_session, dealership.id, old.id, status=ContractStatus.PENDING, signed=False)
    replaced = _legacy_contract(db_session, dealership.id, old.id, status=ContractStatus.PENDING, signed=False)
    _pending_row(db_session, dealership.id, new.id, in_flight.id, age=PENDING_GRACE - dt.timedelta(minutes=5))
    _pending_row(db_session, dealership.id, new.id, replaced.id, age=PENDING_GRACE + dt.timedelta(minutes=5))

    report = backfill(db_session, commit=True)

    db_session.expire_all()
    states = {
        (row.valuation_id, row.contract_id): row.state for row in db_session.scalars(select(ValuationContractCarry)).all()
    }
    assert states[(new.id, in_flight.id)] == ContractCarryState.PENDING
    assert states[(new.id, replaced.id)] == ContractCarryState.RELEASED
    assert report.recent_pending == [(new.id, in_flight.id)]
    assert "RECENT" in render(report, committed=True)


def test_the_backfill_reads_sales_on_a_session_of_its_own(db_session, engine):
    """ADR-047: Sales' answer is read on its own session, which has ended
    before valuation's rows are written — never in the writing transaction."""

    dealership = _dealership(db_session)
    valuation = _valuation(db_session, dealership.id, uuid.uuid4())
    _legacy_contract(db_session, dealership.id, valuation.id, status=ContractStatus.PENDING, signed=False)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    opened: list[Session] = []
    reads_sales_on_writer: list[str] = []

    def sales_session() -> Session:
        session = factory()
        opened.append(session)
        return session

    def on_execute(state):
        statement = str(state.statement)
        if "FROM sales_contract" in statement:
            reads_sales_on_writer.append(statement)

    event.listen(db_session, "do_orm_execute", on_execute)
    try:
        report = backfill(db_session, commit=True, session_factory=sales_session)
    finally:
        event.remove(db_session, "do_orm_execute", on_execute)

    assert report.writes == 1
    assert opened and all(not s.in_transaction() for s in opened), "Sales' session ended"
    assert reads_sales_on_writer == []
