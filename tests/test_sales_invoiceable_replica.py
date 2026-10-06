"""KAN-100 — Sales' is_invoiceable replica (ADR-052) is complete in both
orders, and invoicing (the request_invoice hand-off) refuses a contract
whose vehicle has not been purchased.

Anto's ruling (2026-10-04): a stock vehicle cannot be invoiced unless it is
purchased; a contract CAN be confirmed before that (a manually configured
vehicle moves to pipeline on confirmation, PRD-Stock K-12). There is no
purchase gate at confirmation.

Every purchase here is delivered the way production delivers it: the real
`inventory.stock_item.purchased` outbox row, through app.core.consumer's
consume_once with the handler app.worker registers.
"""

import datetime as dt
import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.consumer import consume_once
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_model import OutboxMessage
from app.customer.schemas.customer import CustomerAddressCreate, CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.consumers import handle_stock_item_purchased_message
from app.sales.models.contract import ContractStatus
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import cancel_contract, confirm_contract, create_contract, request_invoice
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.stock_item_purchase import record_stock_item_purchased

_CONSUMER = "sales.stock_item_purchased"


def _session_factory(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return lambda: factory()


def _dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG", dealer_license_number="ZH-1",
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse",
        address_house_number="1", address_postal_code="8001", address_locality="Zürich", address_canton="ZH",
        phone="+41441234567", tax_id="CHE-123.456.789",
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _customer(db_session, group_id):
    return create_customer(
        db_session, group_id=group_id,
        data=CustomerCreate(
            customer_type="individual", language="de", first_name="Didier", last_name="Perrin",
            emails=[CustomerEmailCreate(email_type="personal", email_address="didier@example.ch", is_primary=True)],
            addresses=[
                CustomerAddressCreate(
                    address_type="domicile", address_street="Bahnhofstrasse", address_house_number="1",
                    address_postal_code="8001", address_locality="Zürich", address_country="CH", is_primary=True,
                )
            ],
        ),
        actor_id=uuid.uuid4(),
        dealership_id=uuid.uuid4(),
    )


def _stock_item(db_session, dealership_id):
    return create_stock_item(
        db_session, tenant_id=dealership_id,
        data=StockItemCreate(vehicle_label="Seat Leon 1.5 eTSI FR DSG", condition=StockItemCondition.USED, vin="1HGCM82633A004352"),
        actor_id=uuid.uuid4(),
    )


def _contract_on(db_session, dealership_id, group_id, item, customer=None):
    customer = customer or _customer(db_session, group_id)
    offer = create_offer(db_session, tenant_id=dealership_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(
            customer_id=customer.id, vehicle_source="stock", stock_item_id=item.id,
            vehicle_label=item.vehicle_label,
        ),
        actor_id=uuid.uuid4(),
    )
    return create_contract(db_session, tenant_id=dealership_id, offer=offer, actor_id=uuid.uuid4())


def _purchase(db_session, item):
    """Books the purchase in Stock; the item already has its VIN, so Stock
    flips is_invoiceable and writes the outbox row in the same commit."""

    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster", supplier_is_vat_registered=False,
            purchase_price=Decimal("20000.00"), purchase_date=dt.date(2026, 8, 1),
        ),
        actor_id=uuid.uuid4(),
    )


def _purchased_message(db_session, item) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.purchased", OutboxMessage.aggregate_id == item.id
        )
    ).one()


def _deliver(db_session, message: OutboxMessage) -> bool:
    return consume_once(db_session, message=message, consumer_name=_CONSUMER, handler=handle_stock_item_purchased_message)


# --- Exit criterion 1 + 3: both orders --------------------------------------


def test_contract_created_after_the_purchase_starts_invoiceable(db_session):
    """The ordinary case for a car that has been on the lot: purchase booked
    and delivered long before anyone writes a contract on it. Before KAN-100
    the one event found no contract, returned, and the contract created
    afterwards stayed is_invoiceable=False forever."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    _purchase(db_session, item)
    assert _deliver(db_session, _purchased_message(db_session, item)) is True

    contract = _contract_on(db_session, dealership.id, group_id, item)

    assert contract.is_invoiceable is True


def test_contract_created_before_the_purchase_becomes_invoiceable_on_delivery(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert contract.is_invoiceable is False

    _purchase(db_session, item)
    _deliver(db_session, _purchased_message(db_session, item))

    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def test_every_contract_on_the_stock_item_is_marked(db_session, engine):
    """A cancelled contract and its replacement both reference the same
    stock item. The consumer used to update whichever row `db.scalar`
    returned first and leave the other stale."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    customer = _customer(db_session, group_id)
    first = _contract_on(db_session, dealership.id, group_id, item, customer)
    cancel_contract(db_session, contract=first, reason="Kunde storniert.", actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    second = _contract_on(db_session, dealership.id, group_id, item, customer)

    _purchase(db_session, item)
    _deliver(db_session, _purchased_message(db_session, item))

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.is_invoiceable is True
    assert second.is_invoiceable is True


def test_the_purchase_is_scoped_to_its_tenant(db_session):
    """Another dealership's purchase of an item with the same id must not
    mark this dealership's contract (rule 7 — tenant from the event, never
    inferred)."""

    dealership = _dealership(db_session)
    other = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.purchased", tenant_id=other.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=item.id, payload={},
        ),
    )
    db_session.commit()
    _deliver(db_session, _purchased_message(db_session, item))

    contract = _contract_on(db_session, dealership.id, group_id, item)

    assert contract.is_invoiceable is False


# --- Exit criterion 3: redelivery --------------------------------------------


def test_redelivery_of_the_same_event_is_a_no_op(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    _purchase(db_session, item)
    message = _purchased_message(db_session, item)

    assert _deliver(db_session, message) is True
    assert _deliver(db_session, message) is False

    rows = db_session.scalars(select(SalesStockItemPurchase).where(SalesStockItemPurchase.stock_item_id == item.id)).all()
    assert len(rows) == 1
    assert rows[0].source_event_id == message.id
    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def test_the_replica_carries_the_stock_number_as_its_display_label(db_session):
    """KAN-150 (rule 2, three-column pattern): the stock number Stock
    publishes on the event, and when Sales copied it."""

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _purchase(db_session, item)
    message = _purchased_message(db_session, item)
    _deliver(db_session, message)

    row = db_session.scalars(select(SalesStockItemPurchase)).one()
    assert row.stock_item_label == item.stock_number
    assert row.stock_item_denorm_refreshed_at == message.occurred_at


def test_a_later_event_refreshes_a_changed_label(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _purchase(db_session, item)
    _deliver(db_session, _purchased_message(db_session, item))

    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.purchased", tenant_id=dealership.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=item.id, payload={"stockNumber": "S-RENUMBERED"},
        ),
    )
    db_session.commit()
    replay = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.purchased",
            OutboxMessage.aggregate_id == item.id,
            OutboxMessage.payload["stockNumber"].as_string() == "S-RENUMBERED",
        )
    ).one()
    _deliver(db_session, replay)

    row = db_session.scalars(select(SalesStockItemPurchase)).one()
    assert row.stock_item_label == "S-RENUMBERED"
    assert row.stock_item_denorm_refreshed_at == replay.occurred_at


def test_the_same_fact_under_a_new_event_id_keeps_one_row(db_session):
    """At-least-once from a producer that re-publishes (a replay): a second
    event id for the same purchase is not a second purchase."""

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _purchase(db_session, item)
    first = _purchased_message(db_session, item)
    _deliver(db_session, first)

    publish(
        db_session,
        OutboxEvent(
            event_type="inventory.stock_item.purchased", tenant_id=dealership.id, producer="inventory",
            aggregate_type="stock_item", aggregate_id=item.id, payload={},
        ),
    )
    db_session.commit()
    replay = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.purchased",
            OutboxMessage.aggregate_id == item.id,
            OutboxMessage.id != first.id,
        )
    ).one()
    assert _deliver(db_session, replay) is True

    rows = db_session.scalars(select(SalesStockItemPurchase).where(SalesStockItemPurchase.stock_item_id == item.id)).all()
    assert len(rows) == 1
    assert rows[0].source_event_id == first.id


def test_the_handler_leaves_the_commit_to_the_harness(db_session):
    """consume_once writes processed_event in the SAME transaction as the
    handler's side effect (app/core/consumer.py). A handler that commits on
    its own makes the side effect durable before the idempotency row: a
    crash between the two leaves a processed fact the harness will deliver
    again. Rolling back after the handler must leave nothing behind."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    _purchase(db_session, item)
    message = _purchased_message(db_session, item)

    handle_stock_item_purchased_message(db_session, message)
    db_session.rollback()

    assert db_session.scalars(select(SalesStockItemPurchase)).all() == []
    db_session.refresh(contract)
    assert contract.is_invoiceable is False


# --- Exit criterion 2 (as ruled 2026-10-04): invoicing, not confirmation ------


def test_confirmation_does_not_require_the_purchase(db_session, engine):
    """PRD-Stock K-12, ruled by Anto 2026-10-04: a contract may be confirmed
    — and the car reserved — before Stock has bought it."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)

    confirmed = confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    assert confirmed.status == ContractStatus.CONFIRMED
    assert confirmed.is_invoiceable is False


def test_request_invoice_refuses_a_stock_vehicle_that_is_not_purchased(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    with pytest.raises(ConflictError) as excinfo:
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())

    assert excinfo.value.details == {"reason": "vehicle_not_purchased"}
    assert db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.aggregate_id == contract.id, OutboxMessage.event_type == "sales.contract.invoice_requested"
        )
    ).all() == []


def test_request_invoice_hands_off_once_the_vehicle_is_purchased(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    _purchase(db_session, item)
    _deliver(db_session, _purchased_message(db_session, item))
    db_session.refresh(contract)

    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())

    assert db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.aggregate_id == contract.id, OutboxMessage.event_type == "sales.contract.invoice_requested"
        )
    ).one()


def test_request_invoice_reads_the_purchase_as_of_now_not_as_of_loading(db_session, engine):
    """The reviewer's interleaving: the purchase lands (another session,
    committed) after this session loaded the contract. With a stored flag
    the contract stayed False for good; derived, the gate sees the row."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    assert contract.is_invoiceable is False

    other = _session_factory(engine)()
    try:
        record_stock_item_purchased(other, tenant_id=dealership.id, stock_item_id=item.id, event_id=uuid.uuid4(), stock_item_label=item.stock_number)
        other.commit()
    finally:
        other.close()

    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())

    assert contract.is_invoiceable is True


def test_request_invoice_refuses_a_manual_configuration_until_sales_knows_it_is_purchased(db_session, engine):
    """A manually configured vehicle becomes a pipeline stock item on
    confirmation (inventory.services.pipeline). Until Sales has learned
    that item (KAN-144, inventory.stock_item.added) and its purchase, it
    may not invoice what it cannot show the dealership owns. Refused, never
    waved through; tests/test_sales_manual_configuration_link.py covers the
    contract becoming invoiceable once both have arrived."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(
            customer_id=customer.id, vehicle_source="manual", vehicle_label="Volkswagen ID.4 Pro",
            manual_vehicle_condition="new", manual_base_price=Decimal("52000.00"),
        ),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))

    with pytest.raises(ConflictError) as excinfo:
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4())

    assert excinfo.value.details == {"reason": "vehicle_not_purchased"}


# --- The migration's backfill ------------------------------------------------


# These call the migrations' backfill() directly, and migrations target
# Postgres only. Their lightweight tables type ids as postgresql.UUID, which on
# SQLite binds 32 hex digits while the app's GUID stores 36-character strings,
# so the backfills' joins and UPDATEs match nothing there.
_postgres_only_migration = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Migration backfill: migrations target Postgres only (ADR-011); SQLite binds the migration's UUIDs differently.",
)


def _load_migration():
    path = next(Path(__file__).resolve().parents[1].glob("alembic/versions/sales/*_stock_item_purchase_replica.py"))
    spec = importlib.util.spec_from_file_location("kan100_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@_postgres_only_migration
def test_the_migration_backfills_from_purchases_already_published(db_session, engine):
    """Purchases Stock published before this table existed are in the
    outbox (never purged). The backfill reads the event log — not Stock's
    table — and re-derives the flag on contracts that already exist."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, group_id, item)
    _purchase(db_session, item)  # published, never delivered to the new table
    message = _purchased_message(db_session, item)

    with engine.begin() as conn:
        _load_migration().backfill(conn)
        _load_migration().backfill(conn)  # re-runnable

    db_session.expire_all()
    rows = db_session.scalars(select(SalesStockItemPurchase)).all()
    assert [(r.tenant_id, r.stock_item_id, r.source_event_id) for r in rows] == [(dealership.id, item.id, message.id)]
    db_session.refresh(contract)
    assert contract.is_invoiceable is True


def _load_label_migration():
    path = next(Path(__file__).resolve().parents[1].glob("alembic/versions/sales/*_stock_item_purchase_label.py"))
    spec = importlib.util.spec_from_file_location("kan150_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@_postgres_only_migration
def test_the_label_migration_backfills_from_the_event_payload(db_session, engine):
    """KAN-150 — replica rows written before the label existed take it from
    their own purchase event's payload (the event log, not Stock's table)."""

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    _purchase(db_session, item)
    message = _purchased_message(db_session, item)
    db_session.add(
        SalesStockItemPurchase(
            tenant_id=dealership.id, stock_item_id=item.id, source_event_id=message.id, recorded_at=message.occurred_at
        )
    )
    legacy_item = create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED, vin="WVWZZZ1KZAW000003"),
        actor_id=uuid.uuid4(),
    )
    db_session.add(SalesStockItemPurchase(tenant_id=dealership.id, stock_item_id=legacy_item.id, source_event_id=None))
    db_session.commit()

    with engine.begin() as conn:
        _load_label_migration().backfill(conn)
        _load_label_migration().backfill(conn)  # re-runnable

    db_session.expire_all()
    rows = {r.stock_item_id: r for r in db_session.scalars(select(SalesStockItemPurchase))}
    assert rows[item.id].stock_item_label == item.stock_number
    assert rows[item.id].stock_item_denorm_refreshed_at == message.occurred_at
    # A legacy row has no event to read; it stays unlabelled rather than
    # reading Stock's table (Anto's ruling on KAN-100's backfill source).
    assert rows[legacy_item.id].stock_item_label is None
