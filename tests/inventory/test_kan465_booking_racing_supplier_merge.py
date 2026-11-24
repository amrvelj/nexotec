"""KAN-465 (review finding): a purchase booking racing a merge of its supplier
contact never leaves the stock item on the merged contact.

Without a lock: the booking reads the contact as live, the merge commits, the
`inventory.customer_merged` consumer runs while the booking is uncommitted and
misses the item, and the booking commits on the merged contact — nothing
moves it afterwards. Both sides now take Stock's advisory lock on the contact
(`services/supplier_lock.py`), the booking before its row lock, the consumer
before it reads. Two sessions, Postgres only (advisory locks), the pattern of
Sales' KAN-657 test.
"""

import datetime as dt
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.inventory import consumers as inventory_consumers
from app.inventory.consumers import handle_customer_merged_message
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import purchase as purchase_service
from app.inventory.services.stock_item import create_stock_item, get_stock_item_for_update_or_404
from app.inventory.services.supplier_lock import lock_supplier_contact
from tests.customer.test_customer_vehicle_party_close_rule import _merge, _run, _wait_until_a_backend_waits_on_a_lock
from tests.inventory.test_inventory_purchase import _make_dealership
from tests.stock_support import supplier_contact


@pytest.fixture
def _postgres(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only; the SQLite lane serialises writers itself")


def _setup(db_session):
    dealership = _make_dealership(db_session)
    duplicate = supplier_contact(db_session, tenant_id=dealership.id, name="Garage Keler", business=True)
    survivor = supplier_contact(db_session, tenant_id=dealership.id, name="Garage Keller", business=True)
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED, vin="WVWZZZ1KZAW465777"),
        actor_id=uuid.uuid4(),
    )
    db_session.commit()
    return dealership, duplicate, survivor, item.id


def _book(session, tenant_id, item_id, contact_id) -> None:
    """What the purchase route does: the supplier lock, then the item's row lock."""

    lock_supplier_contact(session, contact_id)
    item = get_stock_item_for_update_or_404(session, tenant_id, item_id)
    purchase_service.record_purchase(
        session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_contact_id=contact_id, supplier_is_vat_registered=False,
            purchase_price=Decimal("9000.00"), purchase_date=dt.date(2026, 11, 20),
        ),
        actor_id=uuid.uuid4(),
    )


def _consume(session, duplicate_id) -> None:
    message = session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.merged", OutboxMessage.aggregate_id == duplicate_id)
    ).one()
    handle_customer_merged_message(session, message)
    session.commit()


def _pause(monkeypatch, module, name: str, thread: str):
    reached, release = threading.Event(), threading.Event()
    real = getattr(module, name)

    def paused(*args, **kwargs):
        result = real(*args, **kwargs)
        if threading.current_thread().name == thread and not reached.is_set():
            reached.set()
            release.wait(timeout=10)
        return result

    monkeypatch.setattr(module, name, paused)
    return reached, release


def test_a_booking_in_flight_during_the_merge_is_moved_to_the_survivor(_postgres, engine, db_session, monkeypatch):
    dealership, duplicate, survivor, item_id = _setup(db_session)
    reached, release = _pause(monkeypatch, purchase_service, "_supplier_or_422", "booking")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def booking():
        with factory() as session:
            _book(session, dealership.id, item_id, duplicate)

    def consuming():
        with factory() as session:
            _consume(session, duplicate)

    booker = _run("booking", errors, booking)
    try:
        assert reached.wait(timeout=10), "the booking never read its supplier"
        with factory() as merging:
            _merge(merging, duplicate, survivor, dealership.dealer_group_id)  # commits; takes no Stock lock
        consumer = _run("consumer", errors, consuming)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the consumer waits on the booking's lock
        release.set()
        booker.join(timeout=10)
        consumer.join(timeout=10)
    finally:
        release.set()

    assert not booker.is_alive() and not consumer.is_alive()
    assert errors == []
    db_session.expire_all()
    stored = db_session.get(StockItem, item_id)
    assert (stored.supplier_contact_id, stored.supplier_label) == (survivor, "Garage Keller")


def test_a_booking_while_the_consumer_moves_the_duplicates_items_waits_and_is_refused(
    _postgres, engine, db_session, monkeypatch
):
    dealership, duplicate, survivor, item_id = _setup(db_session)
    with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as merging:
        _merge(merging, duplicate, survivor, dealership.dealer_group_id)
    reached, release = _pause(monkeypatch, inventory_consumers, "resolve_live_customer_id", "consumer")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def consuming():
        with factory() as session:
            _consume(session, duplicate)

    def booking():
        with factory() as session:
            _book(session, dealership.id, item_id, duplicate)

    consumer = _run("consumer", errors, consuming)
    try:
        assert reached.wait(timeout=10), "the consumer never took its lock"
        booker = _run("booking", errors, booking)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the booking waits on the consumer's lock
        release.set()
        consumer.join(timeout=10)
        booker.join(timeout=10)
    finally:
        release.set()

    assert not booker.is_alive() and not consumer.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], UnprocessableEntityError)
    assert errors[0].details["reason"] == "supplier_contact_merged"
    db_session.expire_all()
    assert db_session.get(StockItem, item_id).supplier_contact_id is None
