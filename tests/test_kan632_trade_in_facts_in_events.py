"""KAN-632 (ADR-131): customer sits below sales and inventory, so its two
trade-in consumers (`customer.trade_in_purchased`, `customer.trade_in_storno`)
no longer ask either. They take the seller from the event — Sales'
`sales.contract.confirmed` names the buyer and the signature (`customerId`,
`signedAt`), Stock keeps both on a trade-in's pipeline item and carries them
on its purchase and storno events (`tradeInCustomerId`, `tradeInSignedAt`) —
and judge whether an event was overtaken from customer's own record of
Stock's bookings (`customer_stock_item_booking`).

Events already published without the seller (before KAN-632, or for a
pipeline item created before it) are answered through the composition root
(`app/worker.py` asks Sales); the booking record was seeded from the outbox by
the migration, so what was consumed before it is known too.

Postgres lane.
"""

import datetime as dt
import importlib.util
import os
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.consumer import consume_once
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.customer.consumers import (
    OldTradeInEventWithoutSellerLookup,
    handle_stock_item_booking_purchased_message,
    handle_stock_item_purchased_message,
)
from app.customer.models.stock_item_booking import StockItemBooking, StockItemBookingKind
from app.customer.models.vehicle_party import VehiclePartyRole
from app.inventory.models.stock_item import StockItem
from app.sales.models.contract import SalesContract
from app.vehicle.public import resolve_vehicle_mdm_by_vin
from app.worker import register_handlers
from tests.test_consumer_redelivery_idempotency import _RecordingTransport, _registered
from tests.test_kan189_trade_in_without_register import _SIGNED_AT, _bought_trade_in, _customer_consumes, _parties
from tests.test_kan572_trade_in_storno_returned import _buy_again, _customer_consumes_storno, _messages, _storno
from tests.test_sales_lifecycle_reservation import _customer, _dealership

_REPO = Path(__file__).resolve().parents[1]

# --- What the publishers now say ------------------------------------------------


def test_the_confirmation_names_the_buyer_and_the_signature(db_session, engine):
    contract, _item, _purchased = _bought_trade_in(db_session, engine)
    confirmed = _messages(db_session, "sales.contract.confirmed", contract.id)[0]

    assert confirmed.payload["customerId"] == str(contract.customer_id)
    assert confirmed.payload["signedAt"] == _SIGNED_AT.isoformat()  # the fixture's signature, weeks earlier


def test_the_trade_in_item_keeps_its_seller_and_both_stock_events_carry_it(db_session, engine):
    contract, item, purchased = _bought_trade_in(db_session, engine)
    db_session.refresh(item)
    assert (item.trade_in_customer_id, item.trade_in_signed_at) == (contract.customer_id, _SIGNED_AT)

    storno = _storno(db_session, item, returned=False)

    for message in (purchased, storno):  # the same keys on both event types of the stock item
        assert message.payload["tradeInCustomerId"] == str(contract.customer_id)
        assert message.payload["tradeInSignedAt"] == _SIGNED_AT.isoformat()


def test_an_item_that_keeps_no_seller_publishes_none(db_session, engine):
    """A pipeline item created before KAN-632 kept nothing: its events carry
    neither key (no inert nulls) — the consumer's fallback answers them."""

    _contract, item, _purchased = _bought_trade_in(db_session, engine)
    db_session.refresh(item)
    item.trade_in_customer_id = None
    item.trade_in_signed_at = None
    db_session.commit()

    storno = _storno(db_session, item, returned=False)

    assert "tradeInCustomerId" not in storno.payload and "tradeInSignedAt" not in storno.payload


# --- The consumers read the event, never the publisher --------------------------

# A Sales or Stock table read or written (customer_stock_item_booking is customer's own).
_PUBLISHER_TABLE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE)\s+(sales_\w+|stock_item\w*|inventory_\w+)\b", re.IGNORECASE)


class _PublisherTablesTouched:
    """Every SQL statement the consumer runs that names a Sales or Stock table."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if _PUBLISHER_TABLE.search(statement):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)


def _stock_event(db_session, event_type, *, tenant_id, stock_item_id, occurred_at, payload) -> OutboxMessage:
    message = publish(
        db_session,
        OutboxEvent(
            event_type=event_type, tenant_id=tenant_id, producer="inventory", aggregate_type="stock_item",
            aggregate_id=stock_item_id, occurred_at=occurred_at, payload=payload,
        ),
    )
    db_session.commit()
    return message


def _hand_made_trade_in(db_session):
    """A Betrieb, the customer who traded a car in and the car's vehicle-mdm
    record — and no contract, no stock item: nothing of the publishers'."""

    dealership = _dealership(db_session)
    seller = _customer(db_session, dealership.dealer_group_id)
    vehicle = resolve_vehicle_mdm_by_vin(db_session, vin="WVWZZZ1KZAW632001", actor_id=None)
    facts = {
        "stockNumber": "S-632",
        "vehicleId": str(vehicle.id),
        "originContractId": str(uuid.uuid4()),
        "originRole": "trade_in",
        "tradeInCustomerId": str(seller.id),
        "tradeInSignedAt": _SIGNED_AT.isoformat(),
    }
    return dealership, seller, vehicle, facts


def _purchased(db_session, dealership, stock_item_id, facts, *, occurred_at, **extra) -> OutboxMessage:
    return _stock_event(
        db_session, "inventory.stock_item.purchased", tenant_id=dealership.id, stock_item_id=stock_item_id,
        occurred_at=occurred_at, payload={**facts, "purchaseDate": "2026-10-20", "purchasedBy": None, **extra},
    )


def _stornoed(db_session, dealership, stock_item_id, facts, *, occurred_at, returned) -> OutboxMessage:
    return _stock_event(
        db_session, "inventory.stock_item.storno", tenant_id=dealership.id, stock_item_id=stock_item_id,
        occurred_at=occurred_at,
        payload={**facts, "purchaseDate": "2026-10-20", "stornoedBy": None, "returnedToCustomer": returned},
    )


_BOOKED = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)


def test_the_purchase_consumer_takes_the_seller_from_the_event_alone(db_session, engine):
    dealership, seller, vehicle, facts = _hand_made_trade_in(db_session)
    message = _purchased(db_session, dealership, uuid.uuid4(), facts, occurred_at=_BOOKED)

    with _PublisherTablesTouched(engine) as touched:
        assert _customer_consumes(db_session, message) is True

    assert touched.statements == []
    parties = _parties(db_session)
    assert [(p.role, p.customer_id, p.vehicle_id) for p in parties] == [
        (VehiclePartyRole.KEEPER, seller.id, vehicle.id),
        (VehiclePartyRole.OWNER, seller.id, vehicle.id),
    ]
    assert {p.effective_from for p in parties} == {_SIGNED_AT}  # held since the signature the event names
    assert {p.effective_to for p in parties} == {dt.datetime(2026, 10, 19, 22, 0, tzinfo=dt.UTC)}
    assert db_session.scalar(select(func.count()).select_from(StockItem)) == 0
    assert db_session.scalar(select(func.count()).select_from(SalesContract)) == 0


def test_the_storno_consumer_takes_the_seller_from_the_event_alone(db_session, engine):
    dealership, seller, _vehicle, facts = _hand_made_trade_in(db_session)
    stock_item_id = uuid.uuid4()
    _customer_consumes(db_session, _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED))
    storno = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=True
    )

    with _PublisherTablesTouched(engine) as touched:
        assert _customer_consumes_storno(db_session, storno) is True

    assert touched.statements == []
    assert {(p.customer_id, p.effective_to) for p in _parties(db_session)} == {(seller.id, None)}  # reopened


def test_a_purchase_whose_storno_arrived_first_records_nothing(db_session):
    dealership, _seller, _vehicle, facts = _hand_made_trade_in(db_session)
    stock_item_id = uuid.uuid4()
    purchase = _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED)
    storno = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=False
    )

    _customer_consumes_storno(db_session, storno)
    _customer_consumes(db_session, purchase)

    assert _parties(db_session) == []
    kinds = db_session.scalars(select(StockItemBooking.kind).order_by(StockItemBooking.occurred_at)).all()
    assert kinds == [StockItemBookingKind.PURCHASED, StockItemBookingKind.STORNOED]  # both learnt, once each


def test_a_returned_storno_overtaken_by_a_later_purchase_reopens_nobody(db_session):
    dealership, _seller, _vehicle, facts = _hand_made_trade_in(db_session)
    stock_item_id = uuid.uuid4()
    _customer_consumes(db_session, _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED))
    stale = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=True
    )
    _customer_consumes(
        db_session,
        _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=9)),
    )

    _customer_consumes_storno(db_session, stale)

    assert all(p.effective_to is not None for p in _parties(db_session))


def test_another_item_s_bookings_overtake_nothing(db_session):
    dealership, seller, _vehicle, facts = _hand_made_trade_in(db_session)
    _customer_consumes_storno(
        db_session,
        _stornoed(db_session, dealership, uuid.uuid4(), facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=False),
    )

    _customer_consumes(db_session, _purchased(db_session, dealership, uuid.uuid4(), facts, occurred_at=_BOOKED))

    assert {p.customer_id for p in _parties(db_session)} == {seller.id}


# --- Events already in the outbox without the new fields (criterion 3) ----------


def _as_published_before_kan632(db_session, message: OutboxMessage) -> OutboxMessage:
    message.payload = {k: v for k, v in message.payload.items() if k not in {"tradeInCustomerId", "tradeInSignedAt"}}
    db_session.commit()
    return message


def _worker_delivers(db_session, consumer_name: str, message: OutboxMessage) -> bool:
    _event_type, handler = _registered()[consumer_name]
    return consume_once(db_session, message=message, consumer_name=consumer_name, handler=handler)


def test_an_old_purchase_event_is_answered_through_the_worker(db_session, engine):
    contract, item, purchased = _bought_trade_in(db_session, engine)
    old = _as_published_before_kan632(db_session, purchased)

    with _PublisherTablesTouched(engine) as touched:
        assert _worker_delivers(db_session, "customer.trade_in_purchased", old) is True

    # The worker's fallback asked Sales (and the detector the tests above rely on sees it).
    assert any("sales_contract" in statement for statement in touched.statements)
    assert not any(re.search(r"FROM\s+stock_item\b", statement) for statement in touched.statements)
    parties = _parties(db_session)
    assert {(p.customer_id, p.effective_from) for p in parties} == {(contract.customer_id, _SIGNED_AT)}
    assert {p.vehicle_id for p in parties} == {item.vehicle_id}


def test_an_old_returned_storno_is_answered_through_the_worker(db_session, engine):
    contract, item, purchased = _bought_trade_in(db_session, engine)
    _worker_delivers(db_session, "customer.trade_in_purchased", _as_published_before_kan632(db_session, purchased))
    old_storno = _as_published_before_kan632(db_session, _storno(db_session, item, returned=True))

    assert _worker_delivers(db_session, "customer.trade_in_storno", old_storno) is True

    assert {(p.customer_id, p.effective_to) for p in _parties(db_session)} == {(contract.customer_id, None)}


def test_an_old_event_without_a_lookup_fails_loudly_never_drops(db_session, engine):
    """Wiring the customer handler without the worker's fallback is a fault:
    the event is redelivered (and dead-lettered with its alert), not lost."""

    _contract, _item, purchased = _bought_trade_in(db_session, engine)
    old = _as_published_before_kan632(db_session, purchased)

    with pytest.raises(OldTradeInEventWithoutSellerLookup):
        handle_stock_item_purchased_message(db_session, old)
    db_session.rollback()
    assert _parties(db_session) == []


def _seed_sql() -> str:
    path = _REPO / "alembic/versions/customer/c4e8a1f7d932_kan632_stock_item_booking_projection.py"
    spec = importlib.util.spec_from_file_location("kan632_seed_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._SEED


def test_the_migration_seeds_bookings_consumed_before_it_so_a_stale_storno_stays_stale(db_session, engine):
    """P1 consumed → S1 (returned) undelivered → P2 consumed — all before the
    deploy, when no booking record existed. The migration seeds it from the
    outbox; S1, delivered after, is stale and reopens nobody."""

    contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    stale = _storno(db_session, item, returned=True)
    _customer_consumes(db_session, _buy_again(db_session, item, dt.date(2026, 11, 2)))
    db_session.query(StockItemBooking).delete()  # the deploy found no record …
    db_session.execute(text(_seed_sql()))  # … and seeded it from the event log
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(StockItemBooking)) == 3
    labels = set(db_session.scalars(select(StockItemBooking.stock_item_label)).all())
    assert labels == {item.stock_number}

    assert _worker_delivers(db_session, "customer.trade_in_storno", _as_published_before_kan632(db_session, stale)) is True

    assert all(p.effective_to is not None for p in _parties(db_session))
    assert contract.customer_id in {p.customer_id for p in _parties(db_session)}


# --- Rule 10: the new cross-context ids are reconciled ---------------------------


def _check(checks, label):
    return next(c for c in checks if c.label == label)


def test_reconciliation_reports_a_booking_naming_no_stock_item(db_session):
    from app.core.reconciliation import find_orphans
    from app.customer import reconciliation as customer_reconciliation

    dealership, _seller, _vehicle, facts = _hand_made_trade_in(db_session)
    message = _purchased(db_session, dealership, uuid.uuid4(), facts, occurred_at=_BOOKED)
    _customer_consumes(db_session, message)

    check = _check(customer_reconciliation.CHECKS, "customer_stock_item_booking.stock_item_id -> stock_item.id")
    assert find_orphans(db_session, check) == [(message.id, message.aggregate_id)]
    tenant_check = _check(customer_reconciliation.CHECKS, "customer_stock_item_booking.tenant_id -> dealership.id")
    assert find_orphans(db_session, tenant_check) == []


def test_reconciliation_reports_a_trade_in_item_naming_no_customer(db_session, engine):
    from app.core.reconciliation import find_orphans
    from app.inventory import reconciliation as inventory_reconciliation

    _contract, item, _purchased_message = _bought_trade_in(db_session, engine)
    check = _check(inventory_reconciliation.CHECKS, "stock_item.trade_in_customer_id -> customer.id")
    assert find_orphans(db_session, check) == []

    db_session.refresh(item)
    dangling = uuid.uuid4()
    item.trade_in_customer_id = dangling
    db_session.commit()

    assert find_orphans(db_session, check) == [(item.id, dangling)]


# --- The booking record survives a failing trade-in step (review of KAN-632) ----


def _worker_transport(engine) -> InProcessTransport:
    """The customer context's consumers of the two booking events, in the
    order and under the names `register_handlers` gives them."""

    recording = _RecordingTransport()
    register_handlers(recording)
    transport = InProcessTransport(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    for event_type in ("inventory.stock_item.purchased", "inventory.stock_item.storno"):
        for consumer_name, handler in recording._handlers[event_type]:
            if consumer_name.startswith("customer."):
                transport.register(event_type, consumer_name=consumer_name, handler=handler)
    return transport


def test_the_booking_consumers_run_before_the_trade_in_consumers():
    recording = _RecordingTransport()
    register_handlers(recording)

    def order(event_type):
        return [name for name, _handler in recording._handlers[event_type] if name.startswith("customer.")]

    assert order("inventory.stock_item.purchased") == [
        "customer.stock_item_bookings_on_purchased", "customer.trade_in_purchased",
    ]
    assert order("inventory.stock_item.storno") == ["customer.stock_item_bookings_on_storno", "customer.trade_in_storno"]


def _booking_row(db_session, message) -> StockItemBooking | None:
    db_session.expire_all()
    return db_session.get(StockItemBooking, message.id)


def test_a_purchase_whose_trade_in_step_raises_keeps_its_booking_so_a_late_storno_sees_it(
    db_session, engine, monkeypatch
):
    """P1 consumed → S1 (returned) delayed → P2, a re-purchase whose trade-in
    step raises (retried, finally dead-lettered) → S1 delivered. P2's
    booking row comes from its own consumer, so S1 is stale and reopens
    nobody. Before the booking consumers the row was written in the trade-in
    step's transaction and rolled back with it: S1 reopened the customer."""

    dealership, seller, _vehicle, facts = _hand_made_trade_in(db_session)
    transport = _worker_transport(engine)
    stock_item_id = uuid.uuid4()
    transport.deliver(_purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED))
    stale = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=True
    )
    again = _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=9))

    def trade_in_step_fails(*args, **kwargs):
        raise RuntimeError("the trade-in step failed")

    monkeypatch.setattr("app.customer.consumers.record_former_holder_on_purchase", trade_in_step_fails)
    with pytest.raises(DeliveryError) as failed:
        transport.deliver(again)
    assert [name for name, _exc in failed.value.failures] == ["customer.trade_in_purchased"]
    row = _booking_row(db_session, again)
    assert row is not None and row.kind == StockItemBookingKind.PURCHASED
    monkeypatch.undo()

    transport.deliver(stale)

    parties = _parties(db_session)
    assert {p.customer_id for p in parties} == {seller.id}
    assert all(p.effective_to is not None for p in parties)  # the stale storno reopened nobody
    assert _booking_row(db_session, stale) is not None


def test_reconciliation_reports_a_booking_event_without_its_row_after_the_grace(db_session):
    from app.core.reconciliation import find_rows_in_state
    from app.customer import reconciliation as customer_reconciliation

    dealership, _seller, _vehicle, facts = _hand_made_trade_in(db_session)
    stock_item_id = uuid.uuid4()
    purchase = _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED)
    storno = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=False
    )
    check = _check(customer_reconciliation.CHECKS, "stock item booking event with no customer_stock_item_booking row")

    assert find_rows_in_state(db_session, check) == []  # within the grace: the consumer may be on its way

    for message in (purchase, storno):
        message.created_at = utcnow() - dt.timedelta(hours=1, minutes=5)
    db_session.commit()
    assert sorted(find_rows_in_state(db_session, check)) == sorted([(purchase.id, purchase.id), (storno.id, storno.id)])

    consume_once(
        db_session, message=purchase, consumer_name="customer.stock_item_bookings_on_purchased",
        handler=handle_stock_item_booking_purchased_message,
    )
    assert find_rows_in_state(db_session, check) == [(storno.id, storno.id)]


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="the re-seed is Postgres SQL")
def test_the_backfill_adds_the_rows_an_old_worker_left_out_and_a_second_run_adds_none(db_session, engine):
    from scripts import backfill_customer_stock_item_booking as backfill_script

    dealership, _seller, _vehicle, facts = _hand_made_trade_in(db_session)
    stock_item_id = uuid.uuid4()
    consumed = _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED)
    consume_once(
        db_session, message=consumed, consumer_name="customer.stock_item_bookings_on_purchased",
        handler=handle_stock_item_booking_purchased_message,
    )
    # Consumed by a worker without the booking consumers during the deploy:
    stornoed = _stornoed(
        db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=3), returned=False
    )
    bought_again = _purchased(db_session, dealership, stock_item_id, facts, occurred_at=_BOOKED + dt.timedelta(days=9))
    _stock_event(
        db_session, "inventory.stock_item.purchased", tenant_id=None, stock_item_id=uuid.uuid4(),
        occurred_at=_BOOKED, payload={},
    )  # no Betrieb: no booking
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    dry = backfill_script.backfill(commit=False, session_factory=factory)
    assert (dry.rows_before, dry.rows_added) == (1, 2)
    assert db_session.scalar(select(func.count()).select_from(StockItemBooking)) == 1  # the dry run wrote nothing

    done = backfill_script.backfill(commit=True, session_factory=factory)
    assert done.rows_added == 2
    db_session.expire_all()
    rows = {row.event_id: row for row in db_session.scalars(select(StockItemBooking)).all()}
    assert set(rows) == {consumed.id, stornoed.id, bought_again.id}
    assert rows[stornoed.id].kind == StockItemBookingKind.STORNOED
    assert rows[bought_again.id].stock_item_label == "S-632"
    assert rows[bought_again.id].occurred_at == bought_again.occurred_at

    again = backfill_script.backfill(commit=True, session_factory=factory)
    assert (again.rows_before, again.rows_added) == (3, 0)
