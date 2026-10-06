"""KAN-158 — the pipeline item Stock creates for a manually configured
vehicle is reserved for the contract that ordered it (PRD-Stock K-12,
FR-I-11), and the contract's cancellation releases it.

Stock owns both writes (ADR-047: one writer per fact, no cross-context
transaction). It creates the item already reserved, in the same consumer
transaction as the item itself, and releases it when it consumes
`sales.contract.cancelled`. Events are delivered the way production
delivers them: the real outbox rows, through app.core.consumer.consume_once
with the handlers app.worker registers.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.uuid7 import uuid7
from app.inventory.consumers import handle_sales_contract_cancelled_message, handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import handle_sales_contract_confirmed
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract
from app.sales.services.offer import create_offer, update_offer

_CONFIRMED_CONSUMER = "inventory.sales_contract_confirmed"
_CANCELLED_CONSUMER = "inventory.sales_contract_cancelled"


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


def _confirmed_contract(db_session, engine, dealership, *, stock_item: StockItem | None = None):
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    if stock_item is None:
        data = OfferUpdate(
            vehicle_source="manual", vehicle_label="Volkswagen ID.4 Pro", manual_vehicle_condition="new",
            manual_base_price=Decimal("52000.00"),
        )
    else:
        data = OfferUpdate(vehicle_source="stock", stock_item_id=stock_item.id, vehicle_label=stock_item.vehicle_label)
    offer = update_offer(db_session, offer=offer, group_id=group_id, data=data, actor_id=uuid.uuid4())
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    return confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine)
    )


def _message(db_session, event_type, aggregate_id) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
    ).one()


def _event_count(db_session, event_type, aggregate_id) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
    )


def _deliver_confirmed(db_session, message: OutboxMessage) -> bool:
    return consume_once(
        db_session, message=message, consumer_name=_CONFIRMED_CONSUMER, handler=handle_sales_contract_confirmed_message
    )


def _deliver_cancelled(db_session, contract) -> bool:
    return consume_once(
        db_session, message=_message(db_session, "sales.contract.cancelled", contract.id),
        consumer_name=_CANCELLED_CONSUMER, handler=handle_sales_contract_cancelled_message,
    )


def _manual_item(db_session, contract) -> StockItem:
    return db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")).one()


def _re_emission(db_session, original: OutboxMessage) -> OutboxMessage:
    """The same business event under a different eventId — the duplicate
    emission the processed-events table cannot catch."""

    duplicate = OutboxMessage(
        id=uuid7(), event_type=original.event_type, event_version=original.event_version, occurred_at=utcnow(),
        tenant_id=original.tenant_id, producer=original.producer, aggregate_type=original.aggregate_type,
        aggregate_id=original.aggregate_id, correlation_id=uuid7(), causation_id=None, payload=original.payload,
        status=OutboxStatus.PENDING, attempts=0, next_attempt_at=utcnow(),
    )
    db_session.add(duplicate)
    db_session.commit()
    return duplicate


def test_the_manual_configurations_pipeline_item_is_created_reserved_for_its_contract(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_contract(db_session, engine, dealership)

    assert _deliver_confirmed(db_session, _message(db_session, "sales.contract.confirmed", contract.id)) is True

    item = _manual_item(db_session, contract)
    assert item.reservation_state == ReservationState.RESERVED
    assert item.reserved_by_contract_id == contract.id
    assert item.active_reservation_id is not None
    reserved = _message(db_session, "inventory.stock_item.reserved", item.id)
    assert reserved.payload == {"reservationId": str(item.active_reservation_id), "contractId": str(contract.id)}


def test_a_trade_in_pipeline_item_is_not_reserved(db_session):
    """The dealership buys a trade-in; nobody has ordered it."""

    tenant_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    handle_sales_contract_confirmed(
        db_session, tenant_id=tenant_id,
        payload={
            "contractId": str(contract_id), "vehicleSource": "manual",
            "manualConfiguration": {"vehicleLabel": "Škoda Octavia Combi", "condition": "new"},
            "tradeIn": {"vehicleLabel": "Volkswagen Golf", "condition": "used"},
        },
    )
    db_session.commit()

    trade_in = db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract_id}:trade_in")).one()
    assert trade_in.reservation_state == ReservationState.NONE
    assert trade_in.reserved_by_contract_id is None


def test_redelivery_of_the_confirmation_neither_reserves_twice_nor_emits_twice(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_contract(db_session, engine, dealership)
    confirmed = _message(db_session, "sales.contract.confirmed", contract.id)
    assert _deliver_confirmed(db_session, confirmed) is True
    item = _manual_item(db_session, contract)
    reservation_id = item.active_reservation_id

    assert _deliver_confirmed(db_session, confirmed) is False  # same eventId: processed-events stops it
    assert _deliver_confirmed(db_session, _re_emission(db_session, confirmed)) is True  # new eventId: handler runs

    db_session.expire_all()
    item = _manual_item(db_session, contract)
    assert item.reservation_state == ReservationState.RESERVED
    assert item.active_reservation_id == reservation_id
    assert _event_count(db_session, "inventory.stock_item.reserved", item.id) == 1


def test_cancelling_the_contract_releases_its_pipeline_item(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, _message(db_session, "sales.contract.confirmed", contract.id))
    item = _manual_item(db_session, contract)
    reservation_id = item.active_reservation_id

    cancel_contract(db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert _deliver_cancelled(db_session, contract) is True

    db_session.expire_all()
    item = _manual_item(db_session, contract)
    assert item.reservation_state == ReservationState.NONE
    assert item.reserved_by_contract_id is None
    assert item.active_reservation_id is None
    released = _message(db_session, "inventory.stock_item.released", item.id)
    assert released.payload == {"reservationId": str(reservation_id)}


def test_redelivery_of_the_cancellation_releases_once(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, _message(db_session, "sales.contract.confirmed", contract.id))
    item = _manual_item(db_session, contract)
    cancel_contract(db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    cancelled = _message(db_session, "sales.contract.cancelled", contract.id)

    assert _deliver_cancelled(db_session, contract) is True
    assert _deliver_cancelled(db_session, contract) is False
    assert consume_once(
        db_session, message=_re_emission(db_session, cancelled),
        consumer_name=_CANCELLED_CONSUMER, handler=handle_sales_contract_cancelled_message,
    ) is True

    assert _event_count(db_session, "inventory.stock_item.released", item.id) == 1


def test_a_confirmation_re_emitted_after_cancellation_does_not_reserve_again(db_session, engine):
    """A duplicate emission of the confirmation that arrives after the
    cancellation finds the item already there: Stock only reserves an item it
    is creating, so a cancelled contract never gets its car back."""

    dealership = _dealership(db_session)
    contract = _confirmed_contract(db_session, engine, dealership)
    confirmed = _message(db_session, "sales.contract.confirmed", contract.id)
    _deliver_confirmed(db_session, confirmed)
    cancel_contract(db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    _deliver_cancelled(db_session, contract)

    assert _deliver_confirmed(db_session, _re_emission(db_session, confirmed)) is True

    db_session.expire_all()
    item = _manual_item(db_session, contract)
    assert item.reservation_state == ReservationState.NONE
    assert _event_count(db_session, "inventory.stock_item.reserved", item.id) == 1


def test_a_stock_contracts_cancellation_is_released_by_sales_and_the_consumer_finds_nothing(db_session, engine):
    """A stock car's reservation is still released synchronously by
    cancel_contract (ADR-047 Pattern B); the consumer then has nothing left to
    release and emits nothing."""

    dealership = _dealership(db_session)
    stock_car = create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(
            vehicle_label="Audi A4 Avant", condition=StockItemCondition.USED, vin="WAUZZZF40KA000001",
            list_price=Decimal("38000.00"),
        ),
        actor_id=uuid.uuid4(),
    )
    contract = _confirmed_contract(db_session, engine, dealership, stock_item=stock_car)
    cancel_contract(db_session, contract=contract, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert _event_count(db_session, "inventory.stock_item.released", stock_car.id) == 1

    assert _deliver_cancelled(db_session, contract) is True

    db_session.expire_all()
    assert db_session.get(StockItem, stock_car.id).reservation_state == ReservationState.NONE
    assert _event_count(db_session, "inventory.stock_item.released", stock_car.id) == 1


def test_a_cancellation_leaves_another_contracts_reservation_alone(db_session, engine):
    dealership = _dealership(db_session)
    kept = _confirmed_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, _message(db_session, "sales.contract.confirmed", kept.id))
    dropped = _confirmed_contract(db_session, engine, dealership)
    _deliver_confirmed(db_session, _message(db_session, "sales.contract.confirmed", dropped.id))

    cancel_contract(db_session, contract=dropped, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    _deliver_cancelled(db_session, dropped)

    db_session.expire_all()
    assert _manual_item(db_session, kept).reservation_state == ReservationState.RESERVED
    assert _manual_item(db_session, kept).reserved_by_contract_id == kept.id
    assert _manual_item(db_session, dropped).reservation_state == ReservationState.NONE


def test_the_worker_registers_the_cancellation_consumer():
    from app.core.outbox_transport import InProcessTransport
    from app.db import SessionLocal
    from app.worker import register_handlers

    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)
    assert _CANCELLED_CONSUMER in transport.registered_consumer_names()
