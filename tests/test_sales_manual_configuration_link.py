"""KAN-144 — a manually configured vehicle's contract learns the pipeline
stock item its confirmation created, so the KAN-100 purchase gate applies to
it like any stock car: invoiceable once Stock books the purchase, refused
before (Anto, 2026-10-04: "no", it must not stay refused for good).

Events are delivered the way production delivers them: the real outbox
rows, through app.core.consumer.consume_once with the handlers app.worker
registers.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.consumer import consume_once
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_model import OutboxMessage
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.purchase import record_purchase
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.consumers import handle_stock_item_added_message, handle_stock_item_purchased_message
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import confirm_contract, create_contract, request_invoice
from app.sales.services.offer import create_offer, update_offer


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


def _message(db_session, event_type, aggregate_id) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == aggregate_id)
    ).one()


def _confirmed_manual_contract(db_session, engine, dealership):
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(
            vehicle_source="manual", vehicle_label="Volkswagen ID.4 Pro", manual_vehicle_condition="new",
            manual_base_price=Decimal("52000.00"),
        ),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    return confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))


def _stock_creates_the_pipeline_item(db_session, contract) -> StockItem:
    """Inventory consumes sales.contract.confirmed (the real row) and builds
    the pipeline item, publishing inventory.stock_item.added."""

    consume_once(
        db_session, message=_message(db_session, "sales.contract.confirmed", contract.id),
        consumer_name="inventory.sales_contract_confirmed", handler=handle_sales_contract_confirmed_message,
    )
    return db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")).one()


def _deliver_added(db_session, item) -> bool:
    return consume_once(
        db_session, message=_message(db_session, "inventory.stock_item.added", item.id),
        consumer_name="sales.stock_item_added", handler=handle_stock_item_added_message,
    )


def _stock_buys_it(db_session, item):
    """The car arrives (VIN) and its purchase is booked — Stock publishes
    inventory.stock_item.purchased; Sales consumes it."""

    promote_to_vehicle_mdm(db_session, item=item, vin="WVGZZZE2ZNP000004")
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="AMAG Import AG", supplier_is_vat_registered=True,
            purchase_price=Decimal("44000.00"), purchase_date=dt.date(2026, 10, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    consume_once(
        db_session, message=_message(db_session, "inventory.stock_item.purchased", item.id),
        consumer_name="sales.stock_item_purchased", handler=handle_stock_item_purchased_message,
    )


def test_the_contract_learns_its_pipeline_item_and_stays_a_manual_configuration(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    version_before = contract.version
    item = _stock_creates_the_pipeline_item(db_session, contract)

    assert _deliver_added(db_session, item) is True

    db_session.refresh(contract)
    assert contract.stock_item_id == item.id
    assert contract.stock_item_label == item.stock_number
    assert contract.stock_item_denorm_refreshed_at is not None
    assert contract.vehicle_source == "manual"
    assert contract.version == version_before + 1  # a client holding the old version must re-read
    assert contract.is_invoiceable is False


def test_linked_then_purchased_can_be_invoiced(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _deliver_added(db_session, item)
    db_session.refresh(contract)

    with pytest.raises(ConflictError) as excinfo:
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())
    assert excinfo.value.details == {"reason": "vehicle_not_purchased"}

    _stock_buys_it(db_session, item)
    db_session.refresh(contract)

    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())
    assert _message(db_session, "sales.contract.invoice_requested", contract.id)


def test_purchased_then_linked_can_be_invoiced(db_session, engine):
    """Either order works: the purchase is kept per stock item (KAN-100), so
    the link alone makes the contract invoiceable once it lands."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _stock_buys_it(db_session, item)
    db_session.refresh(contract)
    assert contract.is_invoiceable is False  # not linked yet

    _deliver_added(db_session, item)
    db_session.refresh(contract)

    assert contract.is_invoiceable is True
    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())


def test_redelivery_links_once(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    message = _message(db_session, "inventory.stock_item.added", item.id)

    assert _deliver_added(db_session, item) is True
    assert consume_once(db_session, message=message, consumer_name="sales.stock_item_added", handler=handle_stock_item_added_message) is False
    db_session.refresh(contract)
    version = contract.version

    # The handler itself is idempotent too: the same message handled again
    # outside the harness's processed_event guard bumps nothing.
    handle_stock_item_added_message(db_session, message)
    db_session.commit()
    db_session.refresh(contract)
    assert contract.version == version
    assert contract.stock_item_id == item.id


def test_an_event_from_another_dealership_links_nothing(db_session, engine):
    """Rule 7: the tenant comes from the event; another dealership's item
    naming this contract's id must not attach to it."""

    dealership = _dealership(db_session)
    other = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.added", tenant_id=other.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=uuid.uuid4(),
            payload={"stockNumber": "S-999999", "vehicleLabel": "x", "originContractId": str(contract.id), "originRole": "manual_configuration"},
        ),
    )
    db_session.commit()
    message = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "inventory.stock_item.added", OutboxMessage.tenant_id == other.id)
    ).one()

    consume_once(db_session, message=message, consumer_name="sales.stock_item_added", handler=handle_stock_item_added_message)

    db_session.refresh(contract)
    assert contract.stock_item_id is None


def test_a_trade_in_pipeline_item_is_not_linked_as_the_sold_vehicle(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.added", tenant_id=dealership.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=uuid.uuid4(),
            payload={"stockNumber": "S-000777", "vehicleLabel": "VW Golf", "originContractId": str(contract.id), "originRole": "trade_in"},
        ),
    )
    db_session.commit()
    message = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.added",
            OutboxMessage.payload["originRole"].as_string() == "trade_in",
        )
    ).one()

    consume_once(db_session, message=message, consumer_name="sales.stock_item_added", handler=handle_stock_item_added_message)

    db_session.refresh(contract)
    assert contract.stock_item_id is None


def test_a_second_different_item_never_replaces_the_link(db_session, engine):
    """One manual configuration, one pipeline item (Stock's unique
    pipeline_ref). Another item naming the same contract is an integrity
    problem to log, never to overwrite."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _deliver_added(db_session, item)
    db_session.refresh(contract)
    version = contract.version
    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.added", tenant_id=dealership.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=uuid.uuid4(),
            payload={"stockNumber": "S-000999", "vehicleLabel": "x", "originContractId": str(contract.id), "originRole": "manual_configuration"},
        ),
    )
    db_session.commit()
    rogue = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.added",
            OutboxMessage.payload["stockNumber"].as_string() == "S-000999",
        )
    ).one()

    consume_once(db_session, message=rogue, consumer_name="sales.stock_item_added", handler=handle_stock_item_added_message)

    db_session.refresh(contract)
    assert contract.stock_item_id == item.id
    assert contract.version == version


def test_the_link_handler_leaves_the_commit_to_the_harness(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)

    handle_stock_item_added_message(db_session, _message(db_session, "inventory.stock_item.added", item.id))
    db_session.rollback()

    db_session.refresh(contract)
    assert contract.stock_item_id is None


def test_the_worker_registers_the_link_consumer():
    from app.core.outbox_transport import InProcessTransport
    from app.db import SessionLocal
    from app.worker import register_handlers

    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)
    assert "sales.stock_item_added" in transport.registered_consumer_names()
