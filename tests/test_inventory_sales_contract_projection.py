"""KAN-630 (ADR-131, rule 10): Stock's projection of Sales' contracts.

Stock no longer asks Sales; it keeps what its orphan sweep and «Einkauf
stornieren» need from Sales' own facts (`sales.contract.created`,
`.confirmed`, `.invoice_requested`, `.cancelled`), consumed idempotently and
in any order, filled for older contracts by the backfill script, and
compared with Sales every night. On Postgres.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.reconciliation import ReconciliationAlarm
from app.db import SessionLocal
from app.inventory import reconciliation as inventory_reconciliation
from app.inventory.models.sales_contract import (
    InventorySalesContract,
    InventorySalesContractBackfill,
    ProjectedContractStatus,
)
from app.inventory.services.sales_contract import CONSUMERS, ContractFact, apply_contract_fact
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import cancel_contract, request_invoice
from app.worker import register_handlers
from scripts import backfill_inventory_sales_contract_projection as backfill_script
from tests.sales_contract_projection_support import deliver_contract_fact, feed_projection
from tests.test_inventory_reservation_sweep import _confirmed, _contract_for, _item, _session_factory
from tests.test_sales_manual_configuration_link import (
    _confirmed_manual_contract,
    _dealership,
    _deliver_added,
    _stock_buys_it,
    _stock_creates_the_pipeline_item,
)


def _row(db_session, contract_id) -> InventorySalesContract | None:
    db_session.expire_all()
    return db_session.scalars(
        select(InventorySalesContract).where(InventorySalesContract.contract_id == contract_id)
    ).one_or_none()


def _facts(db_session, contract_id) -> dict[str, OutboxMessage]:
    return {
        m.event_type: m
        for m in db_session.scalars(select(OutboxMessage).where(OutboxMessage.aggregate_id == contract_id)).all()
        if m.event_type in CONSUMERS
    }


# --- the consumers -------------------------------------------------------------


def test_the_worker_registers_one_consumer_per_contract_fact():
    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)

    assert set(CONSUMERS.values()) <= transport.registered_consumer_names()
    assert set(CONSUMERS) == {
        "sales.contract.created",
        "sales.contract.confirmed",
        "sales.contract.invoice_requested",
        "sales.contract.cancelled",
    }


def test_the_facts_build_the_contract_whatever_order_they_arrive_in(db_session, engine):
    """Unordered delivery: the cancellation first, then the confirmation,
    then the creation. The status never goes back down; the number comes
    from whichever fact carries it."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    _stock_creates_the_pipeline_item(db_session, contract)
    db_session.commit()
    cancel_contract(
        db_session, contract=contract, reason="Kunde tritt zurück", actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    facts = _facts(db_session, contract.id)

    deliver_contract_fact(db_session, facts["sales.contract.cancelled"])
    deliver_contract_fact(db_session, facts["sales.contract.confirmed"])
    deliver_contract_fact(db_session, facts["sales.contract.created"])

    row = _row(db_session, contract.id)
    assert row is not None
    assert (row.tenant_id, row.status, row.contract_label) == (
        dealership.id, ProjectedContractStatus.CANCELLED, contract.contract_number
    )
    assert row.invoice_requested_at is None and row.stock_item_id is None


def test_an_invoice_request_records_when_and_for_which_car(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _deliver_added(db_session, item)
    _stock_buys_it(db_session, item)
    db_session.refresh(contract)
    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
        session_factory=_session_factory(engine),
    )
    feed_projection(db_session)

    row = _row(db_session, contract.id)
    requested = _facts(db_session, contract.id)["sales.contract.invoice_requested"]
    assert row is not None
    assert (row.status, row.stock_item_id, row.invoice_requested_at) == (
        ProjectedContractStatus.CONFIRMED, item.id, requested.occurred_at
    )


# --- reconciliation (rule 10): a missed fact is caught -----------------------------

_DIFFERS = "inventory_sales_contract differs from its sales_contract"
_MISSING = "sales_contract missing from inventory_sales_contract"


def _findings(db_session) -> dict[str, set]:
    try:
        inventory_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        found: dict[str, set] = {}
        for orphan in alarm.orphans:
            found.setdefault(orphan.check_label, set()).add(orphan.source_row_id)
        return found
    return {}


def _age(db_session, contract_id, hours=2) -> None:
    past = utcnow() - dt.timedelta(hours=hours)
    db_session.execute(
        update(SalesContract).where(SalesContract.id == contract_id).values(created_at=past, updated_at=past)
    )
    db_session.commit()


def test_a_missed_confirmation_is_reported_once_it_is_older_than_the_grace(db_session, engine):
    tenant_id = uuid.uuid4()
    contract = _contract_for(db_session, tenant_id, _item(db_session, tenant_id))
    feed_projection(db_session)  # created: pending
    db_session.execute(
        update(SalesContract).where(SalesContract.id == contract.id).values(status=ContractStatus.CONFIRMED)
    )
    db_session.commit()  # Sales signed; its fact was lost (dead-lettered, never replayed)
    row_id = _row(db_session, contract.id).id

    assert _DIFFERS not in _findings(db_session), "a contract changed within the hour may have its fact in flight"

    _age(db_session, contract.id)

    assert _findings(db_session).get(_DIFFERS) == {row_id}


def test_a_lost_invoice_request_is_reported(db_session, engine):
    tenant_id = uuid.uuid4()
    contract = _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id))
    feed_projection(db_session)
    db_session.execute(
        update(SalesContract).where(SalesContract.id == contract.id).values(invoice_requested_at=utcnow())
    )
    db_session.commit()
    _age(db_session, contract.id)

    assert _findings(db_session).get(_DIFFERS) == {_row(db_session, contract.id).id}


def test_a_contract_the_projection_never_received_is_reported(db_session, engine):
    tenant_id = uuid.uuid4()
    contract = _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id))
    _age(db_session, contract.id)  # nothing fed: every fact of it was missed

    assert _findings(db_session).get(_MISSING) == {contract.id}


def test_a_fed_projection_reports_nothing(db_session, engine):
    tenant_id = uuid.uuid4()
    contract = _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id))
    feed_projection(db_session)
    _age(db_session, contract.id)

    findings = _findings(db_session)
    assert _DIFFERS not in findings and _MISSING not in findings
    assert "inventory_sales_contract.contract_id -> sales_contract.id" not in findings


# --- the backfill --------------------------------------------------------------


@pytest.fixture
def factory(engine):
    return _session_factory(engine)


def test_the_backfill_dry_run_writes_nothing(db_session, engine, factory):
    tenant_id = uuid.uuid4()
    _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id))

    report = backfill_script.backfill(commit=False, session_factory=factory)

    assert (report.contracts_read, report.rows_written) == (1, 1)
    db_session.expire_all()
    assert db_session.scalars(select(InventorySalesContract)).all() == []
    assert db_session.scalars(select(InventorySalesContractBackfill)).all() == []


def test_the_backfill_fills_the_projection_records_the_run_and_never_lowers_a_status(db_session, engine, factory):
    tenant_id = uuid.uuid4()
    confirmed = _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id, label="A"))
    pending = _contract_for(db_session, tenant_id, _item(db_session, tenant_id, label="B"))
    # The consumer already knows `pending` as cancelled (a fact newer than the
    # backfill's read): the backfill's older "pending" must not undo it.
    apply_contract_fact(
        db_session,
        ContractFact(tenant_id=tenant_id, contract_id=pending.id, status=ProjectedContractStatus.CANCELLED),
        observed_at=utcnow(),
    )
    db_session.commit()

    report = backfill_script.backfill(commit=True, session_factory=factory)

    assert report.contracts_read == 2
    assert _row(db_session, confirmed.id).status == ProjectedContractStatus.CONFIRMED
    assert _row(db_session, confirmed.id).contract_label == confirmed.contract_number
    assert _row(db_session, pending.id).status == ProjectedContractStatus.CANCELLED
    assert len(db_session.scalars(select(InventorySalesContractBackfill)).all()) == 1

    again = backfill_script.backfill(commit=True, session_factory=factory)
    assert again.rows_written == 0
