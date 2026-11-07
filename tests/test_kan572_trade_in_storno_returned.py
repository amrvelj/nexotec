"""KAN-572 (Anto, 2026-11-07, option C): a trade-in's purchase is cancelled
(«Einkauf stornieren», KAN-146). The customer's history changes only when the
storno says the car went back to that customer (`returnedToCustomer`): then
the owner and keeper rows the purchase closed (KAN-189) are reopened. Without
it, the history stays as the purchase wrote it — the customer did hand the
car over; only the dealer's booking was reversed.

Postgres lane: the real purchase, storno and both customer consumers, driven
through `consume_once` as the worker does.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.consumer import consume_once
from app.core.errors import UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.customer.consumers import handle_stock_item_storno_message as handle_trade_in_storno_message
from app.customer.models.vehicle_party import VehiclePartyRole
from app.customer.services.customer import allocate_vehicle_party, list_customer_vehicles
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.purchase import PurchaseStornoRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.purchase import record_purchase, storno_purchase
from app.inventory.services.stock_item import create_stock_item
from tests.test_kan189_trade_in_without_register import (
    _TRADE_IN_VIN,
    _bought_trade_in,
    _customer_consumes,
    _parties,
    contract_group_id,
)
from tests.test_sales_lifecycle_reservation import _customer, _dealership

_STORNO_ACTOR = uuid.uuid4()
# KAN-189: the 2026-10-20 purchase closes the rows at the start of that day in Switzerland.
_FIRST_PURCHASE_END = dt.datetime(2026, 10, 19, 22, 0, tzinfo=dt.UTC)


def _messages(db_session, event_type, aggregate_id) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
            .order_by(OutboxMessage.id)
        ).all()
    )


def _storno(db_session, item, *, returned: bool) -> OutboxMessage:
    db_session.refresh(item)
    storno_purchase(
        db_session, item=item,
        data=PurchaseStornoRequest(reason="Kunde hat das Fahrzeug zurückgenommen", returned_to_customer=returned),
        actor_id=_STORNO_ACTOR,
    )
    return _messages(db_session, "inventory.stock_item.storno", item.id)[-1]


def _customer_consumes_storno(db_session, message) -> bool:
    return consume_once(
        db_session, message=message, consumer_name="customer.trade_in_storno", handler=handle_trade_in_storno_message,
    )


def _buy_again(db_session, item, purchase_date: dt.date) -> OutboxMessage:
    db_session.refresh(item)
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="Privat", supplier_is_vat_registered=False, purchase_price=Decimal("8800.00"),
            purchase_date=purchase_date, vin=_TRADE_IN_VIN,
        ),
        actor_id=uuid.uuid4(),
    )
    return _messages(db_session, "inventory.stock_item.purchased", item.id)[-1]


def test_the_storno_event_and_audit_say_whether_the_car_went_back(db_session, engine):
    contract, item, _purchased = _bought_trade_in(db_session, engine)

    message = _storno(db_session, item, returned=True)

    assert message.payload["returnedToCustomer"] is True
    assert message.payload["purchaseDate"] == "2026-10-20"  # the purchase the storno cancelled
    assert message.payload["originContractId"] == str(contract.id)
    assert message.payload["originRole"] == "trade_in"
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == item.id, AuditEvent.action == "purchase_storno")
    ).one()
    assert audit.after["returnedToCustomer"] is True


def test_returned_to_customer_reopens_the_customer_as_owner_and_keeper(db_session, engine):
    contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    assert {p.effective_to for p in _parties(db_session)} == {_FIRST_PURCHASE_END}

    assert _customer_consumes_storno(db_session, _storno(db_session, item, returned=True)) is True

    parties = _parties(db_session)
    assert [(p.role, p.customer_id) for p in parties] == [
        (VehiclePartyRole.KEEPER, contract.customer_id),
        (VehiclePartyRole.OWNER, contract.customer_id),
    ]  # the same two rows, reopened — no second pair
    assert {p.effective_to for p in parties} == {None}
    assert {p.vehicle_id for p in list_customer_vehicles(db_session, customer_id=contract.customer_id)} == {item.vehicle_id}
    audits = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == contract.customer_id, AuditEvent.action == "vehicle_party_update"
        )
    ).all()
    assert {a.reason for a in audits} == {"trade_in_returned_to_customer"} and len(audits) == 2
    assert {a.actor_id for a in audits} == {_STORNO_ACTOR}
    linked = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.vehicle_party.linked")
    ).all()
    assert len(linked) == 4  # two from the purchase, two from the reopen


def test_a_storno_that_does_not_say_so_leaves_the_history(db_session, engine):
    _contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)

    assert _customer_consumes_storno(db_session, _storno(db_session, item, returned=False)) is True

    assert {p.effective_to for p in _parties(db_session)} == {_FIRST_PURCHASE_END}


def test_purchase_storno_purchase_closes_the_reopened_rows_at_the_second_purchase(db_session, engine):
    """The ticket's exit criterion: purchase → storno (returned) → purchase."""

    contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    _customer_consumes_storno(db_session, _storno(db_session, item, returned=True))

    _customer_consumes(db_session, _buy_again(db_session, item, dt.date(2026, 11, 2)))

    parties = _parties(db_session)
    assert [p.customer_id for p in parties] == [contract.customer_id] * 2  # still the same two rows
    # Closed at the start of 2 November in Switzerland (winter time: 23:00 UTC the day before).
    assert {p.effective_to for p in parties} == {dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.UTC)}
    assert list_customer_vehicles(db_session, customer_id=contract.customer_id) == []


def test_a_storno_delivered_before_its_purchase_leaves_no_handover(db_session, engine):
    """Delivery is unordered after a failed delivery: the storno may reach the
    customer context first (nothing to reopen yet), then the purchase. That
    purchase no longer stands in Stock, so it records no handover."""

    _contract, item, purchased = _bought_trade_in(db_session, engine)
    storno = _storno(db_session, item, returned=True)

    assert _customer_consumes_storno(db_session, storno) is True
    assert _customer_consumes(db_session, purchased) is True

    assert _parties(db_session) == []


def test_a_late_first_purchase_never_rewrites_the_one_that_stands(db_session, engine):
    """Bought, stornoed, bought again — and the first purchase event arrives
    last. Only the purchase that stands is recorded."""

    contract, item, first = _bought_trade_in(db_session, engine)
    _customer_consumes_storno(db_session, _storno(db_session, item, returned=False))
    second = _buy_again(db_session, item, dt.date(2026, 11, 2))

    _customer_consumes(db_session, second)
    _customer_consumes(db_session, first)

    assert {p.effective_to for p in _parties(db_session)} == {dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.UTC)}
    assert {p.customer_id for p in _parties(db_session)} == {contract.customer_id}


def test_a_late_returned_storno_after_the_car_was_bought_again_reopens_nobody(db_session, engine):
    """Review finding (2026-11-07): purchase consumed → storno (returned)
    whose delivery fails → the car is bought again → the storno is
    redelivered. Stock holds the car as bought, so the customer is not
    reopened as its holder; the second purchase's history stands."""

    contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    storno = _storno(db_session, item, returned=True)
    second = _buy_again(db_session, item, dt.date(2026, 11, 2))
    _customer_consumes(db_session, second)

    assert _customer_consumes_storno(db_session, storno) is True

    assert list_customer_vehicles(db_session, customer_id=contract.customer_id) == []
    assert all(p.effective_to is not None for p in _parties(db_session))
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.reason == "trade_in_returned_to_customer")
    ).all() == []


def test_the_reopen_closes_a_holder_allocated_since(db_session, engine):
    """No path bypasses the close rule (KAN-148): a customer allocated as owner
    after the purchase stops holding the car when it goes back."""

    contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    group_id = contract_group_id(db_session, contract)
    someone_else = allocate_vehicle_party(
        db_session, vehicle_id=item.vehicle_id, customer_id=_customer(db_session, group_id).id,
        role=VehiclePartyRole.OWNER, group_id=group_id, actor_id=uuid.uuid4(),
    )

    _customer_consumes_storno(db_session, _storno(db_session, item, returned=True))

    db_session.refresh(someone_else)
    assert someone_else.effective_to is not None
    owners_now = [
        p for p in _parties(db_session) if p.role == VehiclePartyRole.OWNER and p.effective_to is None
    ]
    assert [p.customer_id for p in owners_now] == [contract.customer_id]


def test_a_redelivered_storno_reopens_once(db_session, engine):
    _contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    storno = _storno(db_session, item, returned=True)

    assert _customer_consumes_storno(db_session, storno) is True
    assert _customer_consumes_storno(db_session, storno) is False
    # Even past the harness, the handler itself reopens once: the customer holds the car now.
    handle_trade_in_storno_message(db_session, storno)
    db_session.commit()

    assert len(_parties(db_session)) == 2
    assert len(db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "vehicle_party_update")
    ).all()) == 2


def test_only_a_trade_in_can_go_back_to_a_customer(db_session):
    dealership = _dealership(db_session)
    item = create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Seat Ibiza", condition=StockItemCondition.USED), actor_id=uuid.uuid4(),
    )
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="AMAG", supplier_is_vat_registered=True, purchase_price=Decimal("12000.00"),
            purchase_date=dt.date(2026, 10, 1), vin="WVWZZZ6JZ8W572011",
        ),
        actor_id=uuid.uuid4(),
    )

    with pytest.raises(UnprocessableEntityError) as refused:
        _storno(db_session, item, returned=True)

    assert refused.value.details["reason"] == "storno_return_not_trade_in"
    db_session.rollback()
    db_session.refresh(item)
    assert item.purchase_price is not None  # nothing was reversed
    assert _messages(db_session, "inventory.stock_item.storno", item.id) == []
