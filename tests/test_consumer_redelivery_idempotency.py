"""KAN-124 (rule 6, .claude/rules/events.md): every registered consumer is
idempotent on redelivery, proven through the transport.

Delivery is at-least-once: the outbox worker may hand the same message to
the transport again (a crash between the consumer's commit and the
message's `published` mark, a retried poll). `consume_once` is what stops a
second application — but only for a consumer that actually goes through it.
This test takes the consumer list from `app/worker.py::register_handlers`
itself, builds a real message for each from the code that produces it in
production, delivers it twice through `InProcessTransport`, and asserts the
handler ran once, its effect happened once, and one `processed_event` row
exists.

A consumer registered in `register_handlers` with no entry in `_FIXTURES`
fails `test_every_registered_consumer_has_a_redelivery_fixture` — so a new
consumer cannot ship without this test covering it (proven by
`test_an_unfixtured_consumer_is_reported`).
"""

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.consumer import Handler
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.processed_event_model import ProcessedEvent
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.publishing import publish, unpublish
from app.inventory.services.purchase import record_purchase
from app.sales.models.contract import SalesContract
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.services.contract import cancel_contract
from app.worker import register_handlers
from tests.test_inventory_marketplace_transmission import (
    FakeMarketplaceAdapter,
    _make_connection,
    _make_provider,
    _make_ready_item,
    _register_fake,
)
from tests.test_sales_manual_configuration_link import (
    _confirmed_manual_contract,
    _dealership,
    _message,
    _session_factory,
    _stock_creates_the_pipeline_item,
)


class _RecordingTransport(InProcessTransport):
    """Captures what register_handlers registers, delivering nothing."""

    def __init__(self) -> None:
        super().__init__(session_factory=None)  # type: ignore[arg-type]
        self.registrations: dict[str, tuple[str, Handler]] = {}

    def register(self, event_type: str, *, consumer_name: str, handler: Handler) -> None:
        super().register(event_type, consumer_name=consumer_name, handler=handler)
        self.registrations[consumer_name] = (event_type, handler)


def _registered() -> dict[str, tuple[str, Handler]]:
    transport = _RecordingTransport()
    register_handlers(transport)
    assert set(transport.registrations) == transport.registered_consumer_names()
    return transport.registrations


# --- one fixture per consumer --------------------------------------------------------
#
# Each builds the message its consumer receives in production — the real
# outbox row its producer wrote — and returns it with a measure of the
# consumer's effect. The measure must move by exactly one on the first
# delivery and not at all on the second.

Measure = Callable[[Session], int]
Fixture = Callable[[Session, object, pytest.MonkeyPatch], tuple[OutboxMessage, Measure]]


def _contract_confirmed(db_session, engine, monkeypatch):
    """A confirmed manual configuration: Stock builds its pipeline item."""

    contract = _confirmed_manual_contract(db_session, engine, _dealership(db_session))
    message = _message(db_session, "sales.contract.confirmed", contract.id)

    def pipeline_items(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(StockItem)
            .where(StockItem.pipeline_ref == f"contract:{contract.id}:manual")
        )

    return message, pipeline_items


def _contract_cancelled(db_session, engine, monkeypatch):
    """A cancelled manual configuration: Stock releases the pipeline item it
    reserved for the contract, emitting inventory.stock_item.released."""

    contract = _confirmed_manual_contract(db_session, engine, _dealership(db_session))
    item = _stock_creates_the_pipeline_item(db_session, contract)
    db_session.commit()
    cancel_contract(
        db_session, contract=contract, reason="Kunde tritt zurück", actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    message = _message(db_session, "sales.contract.cancelled", contract.id)

    def releases(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.event_type == "inventory.stock_item.released", OutboxMessage.aggregate_id == item.id)
        )

    return message, releases


def _stock_item_added(db_session, engine, monkeypatch):
    """Stock's pipeline item for a manual configuration: Sales links the
    contract to it, bumping the contract's version once."""

    contract = _confirmed_manual_contract(db_session, engine, _dealership(db_session))
    item = _stock_creates_the_pipeline_item(db_session, contract)
    db_session.commit()
    message = _message(db_session, "inventory.stock_item.added", item.id)
    version_before = contract.version

    def link_bumps(db: Session) -> int:
        return db.get(SalesContract, contract.id).version - version_before

    return message, link_bumps


def _stock_item_purchased(db_session, engine, monkeypatch):
    """Stock books the purchase: Sales keeps its replica of the fact."""

    contract = _confirmed_manual_contract(db_session, engine, _dealership(db_session))
    item = _stock_creates_the_pipeline_item(db_session, contract)
    promote_to_vehicle_mdm(db_session, item=item, vin="WVGZZZE2ZNP000004")
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="AMAG Import AG", supplier_is_vat_registered=True,
            purchase_price=Decimal("44000.00"), purchase_date=dt.date(2026, 10, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    db_session.commit()
    message = _message(db_session, "inventory.stock_item.purchased", item.id)

    def replicas(db: Session) -> int:
        return db.scalar(
            select(func.count()).select_from(SalesStockItemPurchase).where(SalesStockItemPurchase.stock_item_id == item.id)
        )

    return message, replicas


def _published_item(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    _make_connection(db_session, _make_provider(db_session), tenant_id=tenant_id)
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004352")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    db_session.commit()
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    return item, adapter


def _stock_item_published(db_session, engine, monkeypatch):
    """A publish: the full feed goes to the marketplace — an external call."""

    item, adapter = _published_item(db_session, monkeypatch)
    return _message(db_session, "inventory.stock_item.published", item.id), lambda db: len(adapter.transmitted)


def _stock_item_unpublished(db_session, engine, monkeypatch):
    """An unpublish: the feed without the item goes to the marketplace."""

    item, adapter = _published_item(db_session, monkeypatch)
    unpublish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, confirm=True, actor_id=uuid.uuid4())
    db_session.commit()
    return _message(db_session, "inventory.stock_item.unpublished", item.id), lambda db: len(adapter.transmitted)


_FIXTURES: dict[str, Fixture] = {
    "inventory.sales_contract_confirmed": _contract_confirmed,
    "inventory.sales_contract_cancelled": _contract_cancelled,
    "sales.stock_item_added": _stock_item_added,
    "sales.stock_item_purchased": _stock_item_purchased,
    "inventory.marketplace_transmission_on_publish": _stock_item_published,
    "inventory.marketplace_transmission_on_unpublish": _stock_item_unpublished,
}


def _consumers_without_fixture(registered: dict[str, tuple[str, Handler]]) -> set[str]:
    return set(registered) - set(_FIXTURES)


def test_every_registered_consumer_has_a_redelivery_fixture():
    assert _consumers_without_fixture(_registered()) == set(), (
        "a consumer registered in app/worker.py has no redelivery fixture here (rule 6, KAN-124)"
    )
    assert set(_FIXTURES) <= set(_registered()), "a fixture names a consumer that is no longer registered"


def test_an_unfixtured_consumer_is_reported():
    transport = _RecordingTransport()
    register_handlers(transport)
    transport.register("probe.thing.happened", consumer_name="probe.unfixtured", handler=lambda db, message: None)

    assert _consumers_without_fixture(transport.registrations) == {"probe.unfixtured"}


@pytest.mark.parametrize("consumer_name", sorted(_registered()))
def test_a_redelivered_message_is_applied_once(consumer_name, db_session, engine, monkeypatch):
    if consumer_name not in _FIXTURES:
        pytest.fail(f"{consumer_name} has no redelivery fixture (rule 6, KAN-124)")
    event_type, handler = _registered()[consumer_name]

    message, measure = _FIXTURES[consumer_name](db_session, engine, monkeypatch)
    assert message.event_type == event_type
    db_session.commit()
    before = measure(db_session)

    calls: list[uuid.UUID] = []

    def counting(db: Session, msg: OutboxMessage) -> None:
        calls.append(msg.id)
        handler(db, msg)

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(event_type, consumer_name=consumer_name, handler=counting)

    transport.deliver(message)
    db_session.expire_all()
    assert measure(db_session) - before == 1, "the first delivery did not apply the consumer's effect"

    transport.deliver(message)
    db_session.expire_all()

    assert calls == [message.id], "the handler ran again on redelivery"
    assert measure(db_session) - before == 1, "the redelivery applied the effect a second time"
    rows = db_session.scalar(
        select(func.count())
        .select_from(ProcessedEvent)
        .where(ProcessedEvent.event_id == message.id, ProcessedEvent.consumer_name == consumer_name)
    )
    assert rows == 1
