"""KAN-122 (with KAN-173): Stock's nightly orphan-reservation sweep. A
reservation is released when its contract is not signed — cancelled,
pending past the safety margin, or missing — and kept for a confirmed or
invoiced contract (Anto, 2026-10-07). Sales' read and Stock's write never
share a transaction (ADR-047)."""

import datetime as dt
import logging
import re
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.base import utcnow
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.core.outbox_model import OutboxMessage
from app.inventory import daily_jobs
from app.inventory.daily_jobs import run_daily_reservation_sweep
from app.inventory.models.stock_item import ReservationState, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import reservation_sweep
from app.inventory.services.reservation import reserve, reserve_for_contract
from app.inventory.services.reservation_sweep import PENDING_MARGIN, release_orphaned_reservations
from app.inventory.services.stock_item import create_stock_item, get_stock_item_or_404
from app.sales.models.contract import ContractStatus
from app.sales.public import get_contract_statuses
from app.sales.schemas.offer import OfferUpdate
from app.sales.services import contract_status
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer

_JOB_NAME = "inventory.orphaned_reservations.release"


def _session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def _item(db_session, tenant_id, label="Cupra Formentor"):
    return create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label=label, condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )


def _contract_for(db_session, tenant_id, item):
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    return create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())


def _confirmed(db_session, engine, tenant_id, item):
    contract = _contract_for(db_session, tenant_id, item)
    return confirm_contract(
        db_session, contract=contract, group_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )


def _set_status(db_session, contract, status):
    """A contract that reached `status` by a path that left its car reserved
    (a failed compensation, a dead-lettered cancellation event)."""

    contract.status = status
    db_session.commit()


def _pending_with_reservation(db_session, tenant_id, item):
    """A confirmation that reserved the car, then failed its own commit and
    its compensating release: the contract is still pending, the car held."""

    contract = _contract_for(db_session, tenant_id, item)
    reserve_for_contract(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=contract.id,
        idempotency_key=f"sales.contract.confirm:{contract.id}",
    )
    return contract


def _state(db_session, tenant_id, item_id):
    db_session.expire_all()
    return get_stock_item_or_404(db_session, tenant_id, item_id).reservation_state


def _later():
    return utcnow() + PENDING_MARGIN + dt.timedelta(minutes=1)


# --- which reservations are orphans ----------------------------------------------------


def test_a_confirmed_contracts_reservation_is_kept(db_session, engine):
    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    _confirmed(db_session, engine, tenant_id, item)

    result = release_orphaned_reservations(db_session, now=_later())

    assert result.released == [] and result.kept_signed == 1
    assert _state(db_session, tenant_id, item.id) == ReservationState.RESERVED


def test_an_invoiced_contracts_reservation_is_kept(db_session, engine):
    """KAN-173: a sold car never goes back to Frei."""

    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    _set_status(db_session, _confirmed(db_session, engine, tenant_id, item), ContractStatus.INVOICED)

    result = release_orphaned_reservations(db_session, now=_later())

    assert result.released == [] and result.kept_signed == 1
    assert _state(db_session, tenant_id, item.id) == ReservationState.RESERVED


def test_a_cancelled_contracts_reservation_is_released(db_session, engine):
    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    _set_status(db_session, _confirmed(db_session, engine, tenant_id, item), ContractStatus.CANCELLED)

    # No margin for a cancelled contract: it is released the night it is found.
    result = release_orphaned_reservations(db_session)

    assert result.released == [item.id]
    assert _state(db_session, tenant_id, item.id) == ReservationState.NONE


def test_a_missing_contracts_reservation_is_released(db_session):
    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k-1")

    result = release_orphaned_reservations(db_session)

    assert result.released == [item.id]
    assert _state(db_session, tenant_id, item.id) == ReservationState.NONE


def test_a_pending_contracts_reservation_is_released_after_the_margin(db_session):
    """Anto, 2026-10-07: an unsigned contract does not hold a car overnight."""

    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    _pending_with_reservation(db_session, tenant_id, item)

    result = release_orphaned_reservations(db_session, now=_later())

    assert result.released == [item.id]
    assert _state(db_session, tenant_id, item.id) == ReservationState.NONE


def test_a_pending_contracts_reservation_inside_the_margin_is_kept(db_session):
    """A confirmation commits the reservation before the contract's own
    CONFIRMED commit; the sweep must not free the car in between."""

    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    _pending_with_reservation(db_session, tenant_id, item)

    result = release_orphaned_reservations(db_session)

    assert result.released == [] and result.kept_recent == 1
    assert _state(db_session, tenant_id, item.id) == ReservationState.RESERVED


def test_another_tenants_contract_counts_as_missing(db_session, engine):
    """Sales answers within the item's tenant only: a contract of another
    dealership is no contract of this one."""

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    foreign = _confirmed(db_session, engine, tenant_a, _item(db_session, tenant_a))
    item = _item(db_session, tenant_b)
    reserve(db_session, tenant_id=tenant_b, stock_item_id=item.id, contract_id=foreign.id, idempotency_key="k-2")

    assert get_contract_statuses(db_session, tenant_id=tenant_b, contract_ids=[foreign.id]) == {}
    result = release_orphaned_reservations(db_session)

    assert result.released == [item.id] and result.kept_signed == 1


# --- one commit per release ------------------------------------------------------------


def test_one_failing_release_does_not_roll_back_the_others(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    items = [_item(db_session, tenant_id, label=f"Car {n}") for n in range(3)]
    for n, item in enumerate(items):
        reserve(
            db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(),
            idempotency_key=f"k-fail-{n}",
        )
    broken = items[1]
    broken_reservation = get_stock_item_or_404(db_session, tenant_id, broken.id).active_reservation_id

    real_release = reservation_sweep.release

    def flaky_release(db, *, tenant_id, reservation_id, idempotency_key):
        if reservation_id == broken_reservation:
            raise RuntimeError("induced failure")
        return real_release(db, tenant_id=tenant_id, reservation_id=reservation_id, idempotency_key=idempotency_key)

    monkeypatch.setattr(reservation_sweep, "release", flaky_release)
    result = release_orphaned_reservations(db_session)

    assert sorted(result.released) == sorted([items[0].id, items[2].id])
    assert result.failed == [broken.id]
    assert _state(db_session, tenant_id, items[0].id) == ReservationState.NONE
    assert _state(db_session, tenant_id, broken.id) == ReservationState.RESERVED
    assert _state(db_session, tenant_id, items[2].id) == ReservationState.NONE


def test_a_second_run_releases_nothing_and_emits_nothing(db_session):
    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k-3")

    first = release_orphaned_reservations(db_session)
    second = release_orphaned_reservations(db_session)

    assert first.released == [item.id] and second.released == [] and second.failed == []
    released_events = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.released", OutboxMessage.aggregate_id == item.id
        )
    ).all()
    assert len(released_events) == 1


def test_a_reservation_changed_since_the_read_is_skipped(db_session, monkeypatch):
    """Released (or made again under a new id) between the sweep's read and
    its release: the release finds no such reservation and leaves the item."""

    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k-4")

    real_list = reservation_sweep._list_reservations

    def list_then_rereserve(db):
        listed = real_list(db)
        reservation_sweep.release(
            db_session, tenant_id=tenant_id, reservation_id=listed[0].reservation_id, idempotency_key="k-4-r"
        )
        reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k-5")
        return listed

    monkeypatch.setattr(reservation_sweep, "_list_reservations", list_then_rereserve)
    result = release_orphaned_reservations(db_session)

    assert result.released == [] and result.changed == 1
    assert _state(db_session, tenant_id, item.id) == ReservationState.RESERVED


# --- ADR-047: no Sales read inside Stock's write transaction ---------------------------

_WRITES_STOCK_ITEM = re.compile(r"^\s*UPDATE\s+stock_item\b", re.IGNORECASE)
_READS_SALES_CONTRACT = re.compile(r"^\s*SELECT\b.*\bFROM\s+sales_contract\b", re.IGNORECASE | re.DOTALL)


def test_no_sales_row_is_read_inside_the_inventory_write_transaction(db_session, engine):
    """Every statement the sweep runs, grouped by the database transaction it
    ran in: none that updates a stock item also reads a Sales contract."""

    tenant_id = uuid.uuid4()
    cancelled_item, missing_item, kept_item = (_item(db_session, tenant_id, label=n) for n in ("A", "B", "C"))
    _set_status(db_session, _confirmed(db_session, engine, tenant_id, cancelled_item), ContractStatus.CANCELLED)
    reserve(
        db_session, tenant_id=tenant_id, stock_item_id=missing_item.id, contract_id=uuid.uuid4(),
        idempotency_key="k-6",
    )
    _confirmed(db_session, engine, tenant_id, kept_item)
    db_session.close()

    transactions: list[list[str]] = []
    open_by_connection: dict[object, list[str]] = {}

    def on_begin(conn):
        open_by_connection[conn] = []
        transactions.append(open_by_connection[conn])

    def on_end(conn):
        open_by_connection.pop(conn, None)

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        if conn not in open_by_connection:
            on_begin(conn)
        open_by_connection[conn].append(statement)

    listeners = [("begin", on_begin), ("commit", on_end), ("rollback", on_end), ("before_cursor_execute", on_execute)]
    for name, fn in listeners:
        event.listen(engine, name, fn)
    try:
        result = release_orphaned_reservations(db_session)
    finally:
        for name, fn in listeners:
            event.remove(engine, name, fn)

    assert sorted(result.released) == sorted([cancelled_item.id, missing_item.id]) and result.kept_signed == 1
    def writes(tx):
        return any(_WRITES_STOCK_ITEM.search(s) for s in tx)

    def reads(tx):
        return any(_READS_SALES_CONTRACT.search(s) for s in tx)

    assert len([tx for tx in transactions if writes(tx)]) == 2, "each release commits on its own"
    assert any(reads(tx) for tx in transactions), "the sweep must ask Sales, or this test proves nothing"
    assert not [tx for tx in transactions if writes(tx) and reads(tx)]


# --- the daily job ---------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_sweep_after_reconciliation(_clean_registry):
    """By name, so deleting the registration fails the build. After
    reconciliation, which records a reservation's missing contract before
    the sweep releases it."""

    worker.register_daily_jobs()
    names = registered_job_names()

    assert _JOB_NAME in names
    assert names.index("reconciliation.run_all") < names.index(_JOB_NAME)


def test_the_daily_job_records_its_counts(db_session, engine, caplog):
    """In the message text: the worker's log format (app/worker.py) prints
    the message only and drops `extra` fields."""

    tenant_id = uuid.uuid4()
    orphan = _item(db_session, tenant_id, label="Orphan")
    reserve(db_session, tenant_id=tenant_id, stock_item_id=orphan.id, contract_id=uuid.uuid4(), idempotency_key="k-7")
    _confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id, label="Sold"))

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_reservation_sweep(db_session)

    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        "inventory.reservation_sweep: released=1 kept_signed=1 kept_recent=0 changed=0 failed=0"
    )


def test_a_failed_release_alarms_without_failing_the_job(db_session, caplog, monkeypatch):
    """Logged at ERROR (the alarm) and returned normally: a raised error
    would re-run the whole sweep every poll cycle."""

    tenant_id = uuid.uuid4()
    item = _item(db_session, tenant_id)
    reserve(db_session, tenant_id=tenant_id, stock_item_id=item.id, contract_id=uuid.uuid4(), idempotency_key="k-8")

    def broken_release(db, **kwargs):
        raise RuntimeError("induced failure")

    monkeypatch.setattr(reservation_sweep, "release", broken_release)
    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_reservation_sweep(db_session)

    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "inventory.reservation_sweep: released=0 kept_signed=0 kept_recent=0 changed=0 failed=1 "
        f"failed_stock_item_ids={item.id}"
    )
    assert _state(db_session, tenant_id, item.id) == ReservationState.RESERVED


# --- the Sales read --------------------------------------------------------------------


def test_contract_statuses_are_read_in_chunks(db_session, engine, monkeypatch):
    """More contracts than one query binds: every one is still answered, and
    an unknown id is simply absent."""

    tenant_id = uuid.uuid4()
    contracts = [_confirmed(db_session, engine, tenant_id, _item(db_session, tenant_id, label=f"C{n}")) for n in range(5)]
    monkeypatch.setattr(contract_status, "_CHUNK", 2)

    statuses = get_contract_statuses(
        db_session, tenant_id=tenant_id, contract_ids=[c.id for c in contracts] + [uuid.uuid4()]
    )

    assert statuses == {c.id: ContractStatus.CONFIRMED for c in contracts}
