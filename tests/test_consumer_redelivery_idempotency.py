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
from app.customer.models.stock_item_booking import StockItemBooking
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.inventory.models.sales_contract import InventorySalesContract, ProjectedContractStatus
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.schemas.purchase import PurchaseStornoRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.publishing import publish, unpublish
from app.inventory.services.purchase import record_purchase, storno_purchase
from app.inventory.services.stock_item import create_stock_item
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.services.contract import cancel_contract, request_invoice
from app.sales.services.invoice_handover import HandoverPlate
from app.sales.services.stock_item_purchase import record_stock_item_purchased
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.worker import register_handlers
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.test_inventory_marketplace_transmission import (
    FakeMarketplaceAdapter,
    _make_connection,
    _make_provider,
    _make_ready_item,
    _register_fake,
)
from tests.test_kan189_trade_in_without_register import _bought_trade_in, _customer_consumes
from tests.test_sales_manual_configuration_link import (
    _confirmed_manual_contract,
    _dealership,
    _deliver_added,
    _message,
    _session_factory,
    _stock_buys_it,
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


def _contract_projection(event_type: str, status: ProjectedContractStatus):
    """KAN-630 — Stock's projection of Sales' contracts takes each of the
    four contract facts from the real producer, once: the measure counts the
    contract's projection row holding what that fact proves (its status; for
    the invoice request, the request and its car too)."""

    def fixture(db_session, engine, monkeypatch):
        dealership = _dealership(db_session)
        contract = _confirmed_manual_contract(db_session, engine, dealership)
        if event_type == "sales.contract.invoice_requested":
            item = _stock_creates_the_pipeline_item(db_session, contract)
            _deliver_added(db_session, item)
            _stock_buys_it(db_session, item)
            db_session.refresh(contract)
            request_invoice(
                db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
                session_factory=_session_factory(engine),
            )
        elif event_type == "sales.contract.cancelled":
            cancel_contract(
                db_session, contract=contract, reason="Kunde tritt zurück", actor_id=uuid.uuid4(),
                session_factory=_session_factory(engine),
            )
        message = _message(db_session, event_type, contract.id)

        def projected(db: Session) -> int:
            stmt = select(func.count()).select_from(InventorySalesContract).where(
                InventorySalesContract.contract_id == contract.id, InventorySalesContract.status == status
            )
            if event_type == "sales.contract.invoice_requested":
                stmt = stmt.where(
                    InventorySalesContract.invoice_requested_at.is_not(None),
                    InventorySalesContract.stock_item_id.is_not(None),
                )
            return db.scalar(stmt)

        return message, projected

    return fixture


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


def _stock_item_reassigned(db_session, engine, monkeypatch):
    """KAN-513: a second contract on the same offer reuses the free pipeline
    car; Sales links it to that contract, bumping its version once."""

    from tests.test_kan513_one_configuration_one_stock_item import _confirm, _first_contract_cancelled, _messages

    _, _, group_id, offer, car = _first_contract_cancelled(db_session, engine)
    second = _confirm(db_session, engine, offer, group_id)
    db_session.commit()
    (message,) = _messages(db_session, "inventory.stock_item.reassigned", car.id)
    version_before = second.version

    def link_bumps(db: Session) -> int:
        return db.get(SalesContract, second.id).version - version_before

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


def _stock_item_storno(db_session, engine, monkeypatch):
    """KAN-146 — Stock stornos a purchase Sales holds: Sales deletes its
    replica once (the measure counts the replica rows that are gone)."""

    contract = _confirmed_manual_contract(db_session, engine, _dealership(db_session))
    item = _stock_creates_the_pipeline_item(db_session, contract)
    promote_to_vehicle_mdm(db_session, item=item, vin="WVGZZZE2ZNP000012")
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="AMAG Import AG", supplier_is_vat_registered=True,
            purchase_price=Decimal("44000.00"), purchase_date=dt.date(2026, 10, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    purchased = _message(db_session, "inventory.stock_item.purchased", item.id)
    record_stock_item_purchased(
        db_session, tenant_id=item.tenant_id, stock_item_id=item.id, event_id=purchased.id,
        stock_item_label=item.stock_number, recorded_at=purchased.occurred_at,
    )
    db_session.commit()
    storno_purchase(db_session, item=item, data=PurchaseStornoRequest(reason="Lieferung storniert"), actor_id=None)
    message = _message(db_session, "inventory.stock_item.storno", item.id)

    def removed_replicas(db: Session) -> int:
        return 1 - db.scalar(
            select(func.count()).select_from(SalesStockItemPurchase).where(SalesStockItemPurchase.stock_item_id == item.id)
        )

    return message, removed_replicas


def _stock_item_commercial_changed(db_session, engine, monkeypatch):
    """KAN-553 — Stock reprices a car an open offer is priced on: Sales flags
    the offer once (one `vehicle_changed_in_stock` audit row)."""

    from app.core.audit_model import AuditEvent
    from tests.test_sales_lifecycle_reservation import _dealership as _plain_dealership
    from tests.test_sales_offer_refinements import _offer_with_vehicle
    from tests.test_sales_offer_refinements import _stock_item as _priced_stock_item
    from tests.test_sales_vehicle_changed_in_stock import _events, _reprice

    dealership = _plain_dealership(db_session)
    item = _priced_stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    _reprice(db_session, item, "36000.00")
    message = _events(db_session, item.id)[-1]

    def flags(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == offer.id, AuditEvent.action == "vehicle_changed_in_stock")
        )

    return message, flags


def _trade_in_purchased(db_session, engine, monkeypatch):
    """KAN-189 — Stock books a trade-in's purchase: the customer context
    records the contract's customer as former owner and keeper, once."""

    _contract, _item, message = _bought_trade_in(db_session, engine)
    db_session.commit()

    def parties(db: Session) -> int:  # the owner row; its keeper twin is written with it
        return db.scalar(
            select(func.count()).select_from(VehicleParty).where(VehicleParty.role == VehiclePartyRole.OWNER)
        )

    return message, parties


def _trade_in_storno(db_session, engine, monkeypatch):
    """KAN-572 — a bought trade-in's purchase is stornoed and the car went
    back to its customer: the customer context reopens that customer as
    owner and keeper, once."""

    _contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    db_session.refresh(item)
    storno_purchase(
        db_session, item=item,
        data=PurchaseStornoRequest(reason="Kunde behält das Fahrzeug", returned_to_customer=True), actor_id=None,
    )
    message = _message(db_session, "inventory.stock_item.storno", item.id)

    def reopened(db: Session) -> int:  # the owner row; its keeper twin is reopened with it
        return db.scalar(
            select(func.count()).select_from(VehicleParty).where(
                VehicleParty.role == VehiclePartyRole.OWNER, VehicleParty.effective_to.is_(None)
            )
        )

    return message, reopened


def _booking_rows(message: OutboxMessage) -> Measure:
    def rows(db: Session) -> int:
        return db.scalar(
            select(func.count()).select_from(StockItemBooking).where(StockItemBooking.event_id == message.id)
        )

    return rows


def _booking_on_purchased(db_session, engine, monkeypatch):
    """KAN-632 — Stock books a purchase: the customer context records the
    booking once (one customer_stock_item_booking row for the event)."""

    _contract, _item, message = _bought_trade_in(db_session, engine)
    db_session.commit()
    return message, _booking_rows(message)


def _booking_on_storno(db_session, engine, monkeypatch):
    """KAN-632 — Stock stornos the purchase: the storno's booking row, once."""

    _contract, item, purchased = _bought_trade_in(db_session, engine)
    _customer_consumes(db_session, purchased)
    db_session.refresh(item)
    storno_purchase(
        db_session, item=item, data=PurchaseStornoRequest(reason="Lieferung storniert"), actor_id=None,
    )
    message = _message(db_session, "inventory.stock_item.storno", item.id)
    return message, _booking_rows(message)


def _customer_merged(db_session, engine, monkeypatch):
    """KAN-185 — a customer merge: Sales moves the duplicate's offer to the
    survivor once (the measure counts the offers on the survivor)."""

    from tests.test_sales_customer_merged_consumer import _customer, _merge, _offer

    tenant_id, group_id = uuid.uuid4(), uuid.uuid4()
    survivor = _customer(db_session, group_id, tenant_id, "Survivor")
    duplicate = _customer(db_session, group_id, tenant_id, "Duplicate")
    _offer(db_session, tenant_id, group_id, duplicate.id)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    def offers_on_survivor(db: Session) -> int:
        return db.scalar(select(func.count()).select_from(SalesOffer).where(SalesOffer.customer_id == survivor.id))

    return message, offers_on_survivor


def _invoice_requested_with_plate(db_session, engine, monkeypatch):
    """KAN-523 — «Rechnung anfordern» with the plate the car leaves with, from
    the real producer: vehicle-mdm records it on the sold car, once."""

    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _deliver_added(db_session, item)
    _stock_buys_it(db_session, item)
    db_session.refresh(contract)
    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
        plate=HandoverPlate(plate="ZH 123456", canton="ZH"), session_factory=_session_factory(engine),
    )
    message = _message(db_session, "sales.contract.invoice_requested", contract.id)
    vehicle_id = item.vehicle_id

    def plates(db: Session) -> int:
        return db.scalar(select(func.count()).select_from(VehiclePlate).where(VehiclePlate.vehicle_id == vehicle_id))

    return message, plates


def _vehicle_summary_changed(db_session, engine, monkeypatch):
    """KAN-186 — a VIN correction on vehicle-mdm, from the real producer: the
    customer's party row naming the car takes the corrected VIN, once."""

    from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
    from app.customer.services.vehicle_party import allocate_vehicle_party
    from app.vehicle.schemas.vehicle_mdm import VehicleMdmUpdate
    from app.vehicle.services.vehicle_mdm import SUMMARY_CHANGED_EVENT, create_vehicle_mdm, update_vehicle_mdm
    from tests.test_sales_lifecycle_reservation import _customer

    group_id = uuid.uuid4()
    vehicle = create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW000186", catalogue_variant_id=None)
    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=_customer(db_session, group_id).id,
        role=VehiclePartyRole.OWNER, group_id=group_id, actor_id=uuid.uuid4(),
    )
    corrected = "WVWZZZ1KZAW000187"
    update_vehicle_mdm(db_session, vehicle=vehicle, data=VehicleMdmUpdate(vin=corrected), actor_id=None, tenant_id=None)
    message = _message(db_session, SUMMARY_CHANGED_EVENT, vehicle.id)

    def relabelled(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(VehicleParty)
            .where(VehicleParty.vehicle_id == vehicle.id, VehicleParty.vehicle_vin == corrected)
        )

    return message, relabelled


def _vehicle_merged(db_session, engine, monkeypatch):
    """KAN-634 — a vehicle merge (FR-V-12), from the real producer: the
    duplicate's party row moves onto the survivor, once."""

    from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
    from app.customer.services.vehicle_party import allocate_vehicle_party
    from app.vehicle.services.merge import MERGED_EVENT, merge_vehicles
    from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
    from tests.test_sales_lifecycle_reservation import _customer

    group_id = uuid.uuid4()
    survivor = create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW000634", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1KZAW000635", catalogue_variant_id=None)
    allocate_vehicle_party(
        db_session, vehicle_id=duplicate.id, customer_id=_customer(db_session, group_id).id,
        role=VehiclePartyRole.OWNER, group_id=group_id, actor_id=uuid.uuid4(),
    )
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    message = _message(db_session, MERGED_EVENT, duplicate.id)

    def on_survivor(db: Session) -> int:
        return db.scalar(select(func.count()).select_from(VehicleParty).where(VehicleParty.vehicle_id == survivor.id))

    return message, on_survivor


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


def _push_user(db_session, monkeypatch):
    """KAN-216: a user with one live Web Push subscription and a recording
    sender in place of the (unbuilt) VAPID adapter."""

    from app.platform.models.auth_session import AuthSession
    from app.platform.models.user import User, UserRole, UserStatus
    from app.platform.reference.services import web_push
    from app.platform.reference.services.web_push import PushOutcome
    from app.platform.services import push_subscriptions

    dealership = _dealership(db_session)
    user = User(
        tenant_id=dealership.id, first_name="Ada", last_name="Push", email=f"push-{uuid.uuid4().hex[:8]}@example.ch",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.flush()
    now = dt.datetime.now(dt.UTC)
    session = AuthSession(
        user_id=user.id, dealership_id=dealership.id, amr=["pwd"], idle_expires_at=now + dt.timedelta(hours=1),
        absolute_expires_at=now + dt.timedelta(hours=2),
    )
    db_session.add(session)
    db_session.flush()
    push_subscriptions.register(
        db_session, user_id=user.id, session_id=session.id, endpoint="https://push.example.test/redelivery",
        p256dh="BPeerKey", auth="authsecret",
    )
    db_session.commit()
    sent: list = []

    class _Recording:
        def public_key(self):
            return "BTestKey"

        def send(self, target, message):
            sent.append(message)
            return PushOutcome.DELIVERED

    monkeypatch.setattr(web_push, "_sender", _Recording())
    return user, sent


def _notification_delivered(db_session, engine, monkeypatch):
    """A delivered security notification: its title and link are pushed."""

    from app.platform.services import notifications
    from app.platform.services.notification_catalogue import NotificationEventCode

    user, sent = _push_user(db_session, monkeypatch)
    row = notifications.notify(
        db_session, NotificationEventCode.ACCOUNT_LOCKED, user_id=user.id, dealership_id=user.tenant_id,
        subject=notifications.NotificationSubject(type="user", id=user.id, link="/account/security"),
    )
    db_session.commit()
    return _message(db_session, notifications.DELIVERED_EVENT, row.id), lambda db: len(sent)


def _digest_released(db_session, engine, monkeypatch):
    """The day's digest, released by the producer itself: one pointer to the
    inbox is pushed."""

    from app.platform.models.notification import Notification
    from app.platform.services import notifications

    user, sent = _push_user(db_session, monkeypatch)
    db_session.add(
        Notification(
            user_id=user.id, dealership_id=user.tenant_id, event_code="security.account_locked", subject_type="user",
            subject_id=user.id, link="/account/security", params={}, delivered_at=None,
        )
    )
    db_session.flush()
    notifications.release_digests(db_session)
    db_session.commit()
    return _message(db_session, notifications.DIGEST_RELEASED_EVENT, user.id), lambda db: len(sent)


def _connected_account_revoked(db_session, engine, monkeypatch):
    """KAN-412 — a revoked connected account, from the real producer: the
    provider's registered revoker ends the grant there once, and the end is
    recorded once (the reference dropped, an audit row)."""

    from app.core.audit_model import AuditEvent
    from app.platform.models.connected_account import (
        ConnectedAccountProvider,
        ConnectedAccountStatus,
        UserConnectedAccount,
    )
    from app.platform.models.user import User, UserRole, UserStatus
    from app.platform.services import connected_account

    dealership = _dealership(db_session)
    user = User(
        tenant_id=dealership.id, first_name="Ada", last_name="Grant", email=f"grant-{uuid.uuid4().hex[:8]}@example.ch",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.flush()
    account = UserConnectedAccount(
        user_id=user.id, provider=ConnectedAccountProvider.MICROSOFT_365, external_account_id=str(uuid.uuid4()),
        scopes=["Mail.Send"], secret_ref=f"infisical://connected-accounts/{uuid.uuid4()}",
        status=ConnectedAccountStatus.ACTIVE, linked_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(account)
    db_session.flush()
    connected_account.revoke(db_session, account=account, user=user, actor_id=user.id, reason=connected_account.REASON_USER)
    db_session.commit()
    ended: list = []
    monkeypatch.setitem(connected_account._REVOKERS, ConnectedAccountProvider.MICROSOFT_365, ended.append)

    def ends_recorded(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == user.id, AuditEvent.action == "connected_account_provider_ended")
        )

    return _message(db_session, connected_account.REVOKED_EVENT, account.id), ends_recorded


def _data_export_requested(db_session, engine, monkeypatch):
    """KAN-218 — a requested export, from the real producer: the worker
    writes its file once and records it once (`produced`); a redelivery
    finds the export no longer requested."""

    import tempfile
    from pathlib import Path

    import app.export_registry  # noqa: F401 - the contexts' exporters
    from app.core import object_store
    from app.core.audit_model import AuditEvent
    from app.platform.models.export_job import ExportJobKind
    from app.platform.services import data_export

    monkeypatch.setattr(object_store, "_store", object_store.LocalDirectoryObjectStore(Path(tempfile.mkdtemp())))
    dealership = _dealership(db_session)
    job = data_export.request_export(
        db_session, dealership=dealership, kind=ExportJobKind.SELF_SERVICE, actor_id=uuid.uuid4()
    )

    def produced(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == job.id, AuditEvent.action == "produced")
        )

    return _message(db_session, data_export.REQUESTED_EVENT, job.id), produced


def _offboarded_betrieb(db_session, monkeypatch):
    """KAN-218 — an offboarded Betrieb with one delivered AutoScout24 listing
    and its connection, and the real producer's `platform.dealership.offboarded` row."""

    from app.inventory.services import marketplace_transmission
    from app.platform.services import offboarding

    dealership = _dealership(db_session)
    connection = _make_connection(db_session, _make_provider(db_session), tenant_id=dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin="1HGCM82633A004218")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    marketplace_transmission.assemble_and_transmit(
        db_session, tenant_id=dealership.id, channel=MarketplaceChannel.AUTOSCOUT24
    )
    offboarding.publish_offboarded(db_session, dealership=dealership, offboarded_at=offboarding.utcnow())
    db_session.commit()
    return _message(db_session, offboarding.OFFBOARDED_EVENT_TYPE, dealership.id), adapter, connection


def _listings_withdrawn_on_offboarded(db_session, engine, monkeypatch):
    """Stock withdraws the live listing: one empty feed to the marketplace."""

    message, adapter, _connection = _offboarded_betrieb(db_session, monkeypatch)
    return message, lambda db: len(adapter.transmitted)


def _disable_audits(connection_id: uuid.UUID) -> Measure:
    from app.core.audit_model import AuditEvent

    def disabled(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == connection_id, AuditEvent.action == "disable")
        )

    return disabled


def _connections_disabled_on_offboarded(db_session, engine, monkeypatch):
    """Integration disables a connection Stock does not publish through (the
    demo vehicle-data provider's): one `disable` audit row."""

    from app.integration.models.provider import IntegrationProvider
    from app.integration.schemas.connection import ConnectionCreate
    from app.integration.services import connections as connection_service

    message, _adapter, _marketplace = _offboarded_betrieb(db_session, monkeypatch)
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="Demo", auth_type="none",
        required_secret_slots=[], capability_codes=["ident_basis"],
    )
    db_session.add(provider)
    db_session.commit()
    connection = connection_service.create_connection(
        db_session, tenant_id=message.tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="Demo", environment="sandbox"),
        actor_id=uuid.uuid4(),
    )
    return message, _disable_audits(connection.id)


def _marketplace_connections_disabled_on_withdrawn(db_session, engine, monkeypatch):
    """KAN-218 — Stock's withdrawn fact from the real producer (its consumer of
    the offboarded fact): Integration disables the marketplace connection,
    one `disable` audit row."""

    from app.inventory.consumers import handle_dealership_offboarded_message

    offboarded, _adapter, connection = _offboarded_betrieb(db_session, monkeypatch)
    handle_dealership_offboarded_message(db_session, offboarded)
    db_session.commit()
    return _message(db_session, "inventory.dealership_listings.withdrawn", offboarded.tenant_id), _disable_audits(
        connection.id
    )


def _configuration_taken(event_type: str, take) -> Fixture:
    """KAN-270 — a host takes a configuration, from the real producer:
    vehicle stamps it taken, once (`first_taken_at`, never moved)."""

    def fixture(db_session, engine, monkeypatch):
        from app.vehicle.models.configuration import VehicleConfiguration
        from tests.test_configurator_hosts import _dealership as _hosts_dealership

        configuration_id, aggregate_id = take(db_session, _hosts_dealership(db_session))

        def stamped(db: Session) -> int:
            return db.scalar(
                select(func.count())
                .select_from(VehicleConfiguration)
                .where(VehicleConfiguration.id == configuration_id, VehicleConfiguration.first_taken_at.is_not(None))
            )

        return _message(db_session, event_type, aggregate_id), stamped

    return fixture


def _offer_takes(db_session, dealership):
    from app.sales.schemas.offer import OfferUpdate
    from app.sales.services.offer import create_offer, update_offer
    from tests.test_configurator_hosts import _build_configuration

    config = _build_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    update_offer(db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None)
    return config.id, offer.id


def _contract_takes(db_session, dealership):
    from app.sales.schemas.contract import ContractUpdate
    from app.sales.services.contract import update_contract
    from tests.test_configurator_hosts import _build_configuration
    from tests.test_sales_contract_edit import _customer_born_contract

    config = _build_configuration(db_session, dealership.id)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    update_contract(
        db_session, contract=contract, group_id=uuid.uuid4(), data=ContractUpdate(configuration_id=config.id), actor_id=None
    )
    return config.id, contract.id


def _stock_item_added_takes(db_session, dealership):
    from tests.test_configurator_hosts import _record_configuration
    from tests.test_vehicle_configuration_retention import _pipeline_item

    config = _record_configuration(db_session, dealership.id)
    return config.id, _pipeline_item(db_session, dealership.id, config.id).id


def _stock_item_attach_takes(db_session, dealership):
    from app.inventory.services.stock_item import attach_configuration
    from tests.test_configurator_hosts import _record_configuration
    from tests.test_vehicle_configuration_retention import _pipeline_item

    config = _record_configuration(db_session, dealership.id)
    item = _pipeline_item(db_session, dealership.id, None)
    attach_configuration(db_session, item=item, configuration_id=config.id, actor_id=uuid.uuid4())
    return config.id, item.id


def _valuation_takes(db_session, dealership):
    from tests.test_configurator_hosts import _record_configuration
    from tests.test_vehicle_configuration_retention import _valuation

    config = _record_configuration(db_session, dealership.id)
    return config.id, _valuation(db_session, dealership.id, config.id).id


def _stock_holding_item(db_session) -> StockItem:
    """KAN-667 — a car bought into stock through the production path (the
    booking promotes it: `vin_assigned`)."""

    dealership = _make_dealership(db_session)
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED, vin="WVWZZZ1KZAW667101"),
        actor_id=uuid.uuid4(),
    )
    item = record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster", supplier_is_vat_registered=False,
            purchase_price=Decimal("20000.00"), purchase_date=dt.date(2026, 11, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    db_session.commit()
    return item


def _stock_holding_rows(item_id: uuid.UUID, column) -> Measure:
    def rows(db: Session) -> int:
        return db.scalar(
            select(func.count())
            .select_from(VehicleStockHolding)
            .where(VehicleStockHolding.stock_item_id == item_id, column.is_not(None))
        )

    return rows


def _stock_holding_on_vin_assigned(db_session, engine, monkeypatch):
    """KAN-667 — promotion gives the stock item its record: Vehicle's
    projection holds it once (one row naming the record)."""

    item = _stock_holding_item(db_session)
    message = _message(db_session, "inventory.pipeline_vehicle.vin_assigned", item.id)
    return message, _stock_holding_rows(item.id, VehicleStockHolding.vehicle_id)


def _stock_holding_on_sold(db_session, engine, monkeypatch):
    """KAN-667 — the car is sold (Finance's invoice): the projection records
    once that it left stock."""

    item = _stock_holding_item(db_session)
    apply_finance_invoice_issued(db_session, tenant_id=item.tenant_id, stock_item_id=item.id, invoice_ref="INV-667")
    message = _message(db_session, "inventory.stock_item.sold", item.id)
    return message, _stock_holding_rows(item.id, VehicleStockHolding.left_stock_at)


_FIXTURES: dict[str, Fixture] = {
    "platform.connected_account_provider_revocation": _connected_account_revoked,
    "platform.data_export_production": _data_export_requested,
    "inventory.listings_withdrawn_on_offboarded": _listings_withdrawn_on_offboarded,
    "integration.connections_disabled_on_offboarded": _connections_disabled_on_offboarded,
    "integration.marketplace_connections_disabled_on_withdrawn": _marketplace_connections_disabled_on_withdrawn,
    "inventory.sales_contract_confirmed": _contract_confirmed,
    "inventory.sales_contract_cancelled": _contract_cancelled,
    "inventory.sales_contract_projection_on_created": _contract_projection(
        "sales.contract.created", ProjectedContractStatus.PENDING
    ),
    "inventory.sales_contract_projection_on_confirmed": _contract_projection(
        "sales.contract.confirmed", ProjectedContractStatus.CONFIRMED
    ),
    "inventory.sales_contract_projection_on_invoice_requested": _contract_projection(
        "sales.contract.invoice_requested", ProjectedContractStatus.CONFIRMED
    ),
    "inventory.sales_contract_projection_on_cancelled": _contract_projection(
        "sales.contract.cancelled", ProjectedContractStatus.CANCELLED
    ),
    "sales.stock_item_added": _stock_item_added,
    "sales.stock_item_purchased": _stock_item_purchased,
    "sales.stock_item_storno": _stock_item_storno,
    "sales.stock_item_commercial_changed": _stock_item_commercial_changed,
    "customer.stock_item_bookings_on_purchased": _booking_on_purchased,
    "customer.stock_item_bookings_on_storno": _booking_on_storno,
    "customer.trade_in_purchased": _trade_in_purchased,
    "customer.trade_in_storno": _trade_in_storno,
    "sales.customer_merged": _customer_merged,
    "sales.stock_item_reassigned": _stock_item_reassigned,
    "inventory.marketplace_transmission_on_publish": _stock_item_published,
    "inventory.marketplace_transmission_on_unpublish": _stock_item_unpublished,
    "platform.web_push_on_notification": _notification_delivered,
    "platform.web_push_on_digest": _digest_released,
    "vehicle.plate_on_invoice_requested": _invoice_requested_with_plate,
    "customer.vehicle_party_labels_on_summary_changed": _vehicle_summary_changed,
    "customer.vehicle_parties_on_vehicle_merged": _vehicle_merged,
    "vehicle.configuration_taken_by_offer": _configuration_taken("sales.offer.configuration_attached", _offer_takes),
    "vehicle.configuration_taken_by_contract": _configuration_taken(
        "sales.contract.configuration_attached", _contract_takes
    ),
    "vehicle.configuration_taken_by_added_stock_item": _configuration_taken(
        "inventory.stock_item.added", _stock_item_added_takes
    ),
    "vehicle.configuration_taken_by_stock_item": _configuration_taken(
        "inventory.stock_item.configuration_attached", _stock_item_attach_takes
    ),
    "vehicle.configuration_taken_by_valuation": _configuration_taken("valuation.created", _valuation_takes),
    "vehicle.stock_holding_on_vin_assigned": _stock_holding_on_vin_assigned,
    "vehicle.stock_holding_on_sold": _stock_holding_on_sold,
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
