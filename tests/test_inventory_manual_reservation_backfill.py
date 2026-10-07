"""KAN-166 — the backfill for manual configurations Stock handled before
KAN-158: their pipeline items were created unreserved, and their contracts'
cancellations were never recorded, because neither consumer step existed.

Stock learns which contracts are cancelled from the `sales.contract.cancelled`
events Sales published (still in the outbox), never from Sales' tables. The
pre-KAN-158 state is built by running the real Sales flow and then undoing
exactly what KAN-158 added: the item's reservation, and the delivery of the
cancellation.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.core.uuid7 import uuid7
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.stock_item import ReservationState, StockItem
from app.inventory.services.reservation import release_and_flush
from app.inventory.services.reservation_backfill import BackfillOutcome, backfill_manual_configuration_reservations
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer

pytestmark = pytest.mark.usefixtures("db_session")


def _session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def _dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG", dealer_license_number=f"ZH-{uuid.uuid4().hex[:6]}",
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse",
        address_house_number="1", address_postal_code="8001", address_locality="Zürich", address_canton="ZH",
        phone="+41441234567", tax_id="CHE-123.456.789",
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _confirmed_manual_contract(db_session, engine, dealership):
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    data = OfferUpdate(
        vehicle_source="manual", vehicle_label="Volkswagen ID.4 Pro", manual_vehicle_condition="new",
        manual_base_price=Decimal("52000.00"),
    )
    offer = update_offer(db_session, offer=offer, group_id=group_id, data=data, actor_id=uuid.uuid4())
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    return confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )


def _message(db_session, event_type, aggregate_id) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
    ).one()


def _deliver_confirmed(db_session, contract) -> None:
    assert consume_once(
        db_session, message=_message(db_session, "sales.contract.confirmed", contract.id),
        consumer_name="inventory.sales_contract_confirmed", handler=handle_sales_contract_confirmed_message,
    )


def _manual_item(db_session, contract) -> StockItem:
    return db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")).one()


def _as_before_kan_158(db_session, item: StockItem) -> None:
    """The item as Stock created it before KAN-158: unreserved."""

    item.reservation_state = ReservationState.NONE
    item.reserved_by_contract_id = None
    item.active_reservation_id = None
    db_session.commit()


def _pre_kan_158_manual_item(db_session, engine, dealership):
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, contract)
    item = _manual_item(db_session, contract)
    _as_before_kan_158(db_session, item)
    return contract, item


def _events(db_session, event_type) -> int:
    return db_session.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type == event_type))


def _cancel(db_session, engine, contract) -> None:
    """Cancelled before KAN-158: the event is published, no Stock consumer ever handled it."""

    cancel_contract(
        db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )


def _outcome(report, contract_id) -> BackfillOutcome:
    (line,) = [line for line in report.lines if line.contract_id == contract_id]
    return line.outcome


def test_a_confirmed_contracts_unreserved_item_is_reserved_once(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    reserved_before = _events(db_session, "inventory.stock_item.reserved")

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.RESERVED
    db_session.expire_all()
    item = db_session.get(StockItem, item.id)
    assert item.reservation_state == ReservationState.RESERVED
    assert item.reserved_by_contract_id == contract.id
    assert item.active_reservation_id is not None
    event = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.reserved", OutboxMessage.aggregate_id == item.id
        ).order_by(OutboxMessage.created_at.desc())
    ).first()
    assert event.payload == {"reservationId": str(item.active_reservation_id), "contractId": str(contract.id)}
    assert _events(db_session, "inventory.stock_item.reserved") == reserved_before + 1


def test_re_running_the_backfill_is_a_no_op(db_session, engine):
    dealership = _dealership(db_session)
    confirmed, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    cancelled, _ = _pre_kan_158_manual_item(db_session, engine, dealership)
    _cancel(db_session, engine, cancelled)
    backfill_manual_configuration_reservations(db_session, commit=True)
    db_session.expire_all()
    version = db_session.get(StockItem, item.id).version
    outbox_before = db_session.scalar(select(func.count()).select_from(OutboxMessage))
    records_before = db_session.scalar(select(func.count()).select_from(InventoryCancelledContract))

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, confirmed.id) == BackfillOutcome.ALREADY_RESERVED
    assert _outcome(report, cancelled.id) == BackfillOutcome.CANCELLATION_ALREADY_RECORDED
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).version == version
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == outbox_before
    assert db_session.scalar(select(func.count()).select_from(InventoryCancelledContract)) == records_before
    assert not report.needs_attention


def test_a_cancelled_contract_is_recorded_and_its_item_stays_unreserved(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    _cancel(db_session, engine, contract)
    cancelled_event = _message(db_session, "sales.contract.cancelled", contract.id)
    reserved_before = _events(db_session, "inventory.stock_item.reserved")

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.CANCELLATION_RECORDED
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE
    record = db_session.scalars(
        select(InventoryCancelledContract).where(InventoryCancelledContract.contract_id == contract.id)
    ).one()
    assert record.tenant_id == dealership.id
    assert record.contract_label == contract.contract_number
    assert record.cancelled_at == cancelled_event.occurred_at
    assert _events(db_session, "inventory.stock_item.reserved") == reserved_before


def test_a_confirmation_replayed_after_the_backfill_for_a_cancelled_contract_creates_its_item_unreserved(
    db_session, engine
):
    """KAN-158 re-review finding 5: the confirmation's first delivery failed
    before KAN-158 and is retried after it, for a contract cancelled before it."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)  # confirmation not delivered
    _cancel(db_session, engine, contract)

    report = backfill_manual_configuration_reservations(db_session, commit=True)
    assert _outcome(report, contract.id) == BackfillOutcome.CANCELLATION_RECORDED

    _deliver_confirmed(db_session, contract)
    db_session.expire_all()
    assert _manual_item(db_session, contract).reservation_state == ReservationState.NONE


def test_an_item_reserved_by_another_contract_is_reported_not_overwritten(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    other_contract_id, other_reservation_id = uuid.uuid4(), uuid7()
    item.reservation_state = ReservationState.RESERVED
    item.reserved_by_contract_id = other_contract_id
    item.active_reservation_id = other_reservation_id
    db_session.commit()
    version = item.version

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.HELD_BY_OTHER_CONTRACT
    assert report.needs_attention
    db_session.expire_all()
    item = db_session.get(StockItem, item.id)
    assert item.reserved_by_contract_id == other_contract_id
    assert item.active_reservation_id == other_reservation_id
    assert item.version == version


def test_an_item_without_a_confirmation_event_is_reported_not_reserved(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    db_session.delete(_message(db_session, "sales.contract.confirmed", contract.id))
    db_session.commit()

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.NO_CONFIRMATION_EVENT
    assert report.needs_attention
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE


def test_a_dry_run_reports_and_writes_nothing(db_session, engine):
    dealership = _dealership(db_session)
    confirmed, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    cancelled, _ = _pre_kan_158_manual_item(db_session, engine, dealership)
    _cancel(db_session, engine, cancelled)
    outbox_before = db_session.scalar(select(func.count()).select_from(OutboxMessage))

    report = backfill_manual_configuration_reservations(db_session, commit=False)

    assert _outcome(report, confirmed.id) == BackfillOutcome.RESERVED
    assert _outcome(report, cancelled.id) == BackfillOutcome.CANCELLATION_RECORDED
    assert report.committed is False
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == outbox_before
    assert db_session.scalar(select(func.count()).select_from(InventoryCancelledContract)) == 0


def test_the_script_runs_dry_by_default_and_exits_zero_when_nothing_needs_attention(db_session, engine, capsys, monkeypatch):
    import scripts.backfill_manual_configuration_reservations as script

    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    monkeypatch.setattr(script, "SessionLocal", _session_factory(engine))

    assert script.main([]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE

    assert script.main(["--commit"]) == 0
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reserved_by_contract_id == contract.id


def test_an_invoiced_car_that_has_left_stock_is_reserved_too(db_session, engine):
    """Anto, 2026-10-07: an invoiced contract's car is reserved like a confirmed one."""

    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    item.left_stock_at = utcnow()
    db_session.commit()

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.RESERVED
    db_session.expire_all()
    item = db_session.get(StockItem, item.id)
    assert item.reserved_by_contract_id == contract.id
    assert item.left_stock_at is not None


def test_an_item_released_before_is_reported_not_reserved_again(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    backfill_manual_configuration_reservations(db_session, commit=True)
    db_session.expire_all()
    release_and_flush(db_session, item=db_session.get(StockItem, item.id))  # released on purpose, e.g. via the API
    db_session.commit()
    reserved_before = _events(db_session, "inventory.stock_item.reserved")

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.RELEASED_BEFORE
    assert report.needs_attention
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE
    assert _events(db_session, "inventory.stock_item.reserved") == reserved_before


def test_an_item_in_another_tenant_than_its_confirmation_is_reported(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    item.tenant_id = _dealership(db_session).id
    db_session.commit()

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.TENANT_MISMATCH
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE


def test_a_cancellation_stock_recorded_without_a_sales_event_is_reported(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    db_session.add(
        InventoryCancelledContract(
            tenant_id=dealership.id, contract_id=contract.id, contract_label=contract.contract_number,
            contract_denorm_refreshed_at=utcnow(), cancelled_at=utcnow(),
        )
    )
    db_session.commit()

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.CANCELLATION_RECORDED_WITHOUT_EVENT
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE


def test_a_cancellation_without_a_tenant_is_reported_and_the_contract_never_treated_as_live(db_session, engine):
    dealership = _dealership(db_session)
    contract, item = _pre_kan_158_manual_item(db_session, engine, dealership)
    _cancel(db_session, engine, contract)
    _message(db_session, "sales.contract.cancelled", contract.id).tenant_id = None
    db_session.commit()

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    assert _outcome(report, contract.id) == BackfillOutcome.CANCELLATION_WITHOUT_TENANT
    assert report.needs_attention
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.NONE
    assert db_session.scalar(select(func.count()).select_from(InventoryCancelledContract)) == 0


def test_recording_a_cancellation_reports_the_items_it_releases(db_session, engine):
    """A post-KAN-158 item whose cancellation message was never consumed (pending or dead-lettered)."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, contract)  # created reserved, as KAN-158 does
    _cancel(db_session, engine, contract)

    report = backfill_manual_configuration_reservations(db_session, commit=True)

    (line,) = [line for line in report.lines if line.contract_id == contract.id]
    assert line.outcome == BackfillOutcome.CANCELLATION_RECORDED
    assert line.detail == "released 1 item(s)"
    db_session.expire_all()
    assert _manual_item(db_session, contract).reservation_state == ReservationState.NONE


def test_the_script_exits_one_when_something_needs_attention(db_session, engine, capsys, monkeypatch):
    import scripts.backfill_manual_configuration_reservations as script

    dealership = _dealership(db_session)
    contract, _item = _pre_kan_158_manual_item(db_session, engine, dealership)
    db_session.delete(_message(db_session, "sales.contract.confirmed", contract.id))
    db_session.commit()
    monkeypatch.setattr(script, "SessionLocal", _session_factory(engine))

    assert script.main([]) == 1
    out = capsys.readouterr().out
    assert "NEEDS ATTENTION: 1" in out
    assert f"contract {contract.id}" in out
