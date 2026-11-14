"""KAN-218 (Anto, 2026-11-14, Q3 A): offboarding withdraws every live
marketplace listing and disables every integration connection of the Betrieb.

Platform publishes `platform.dealership.offboarded` in the offboarding
transaction and calls neither context (ADR-131, rule 12). Stock's consumer
sends the marketplace an empty feed through the gateway, unpublishes the
listings and states `inventory.dealership_listings.withdrawn`; Integration
disables every other connection on the offboarded fact and the connections
Stock publishes through only on Stock's — delivery is per consumer and
unordered, so no order of registrations is relied on. Each on its own
session, idempotent by eventId; a sister Betrieb's listings and connections
are untouched. Delivered here through the worker's own registrations.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.integration.models.connection import ConnectionEnvironment, ConnectionStatus, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import secrets_backend
from app.inventory.models.stock_item_publishing import (
    MarketplaceChannel,
    PublishingState,
    StockItemPublishing,
    TransmissionStatus,
)
from app.inventory.services import marketplace_transmission
from app.inventory.services.listing_withdrawal import ListingWithdrawalBlocked, withdraw_live_listings
from app.inventory.services.publishing import publish
from app.platform.services import offboarding
from tests.test_inventory_marketplace_transmission import (
    FakeMarketplaceAdapter,
    _make_connection,
    _make_provider,
    _make_ready_item,
    _register_fake,
)
from tests.test_sales_manual_configuration_link import _dealership

INVENTORY = "inventory.listings_withdrawn_on_offboarded"
INTEGRATION = "integration.connections_disabled_on_offboarded"
INTEGRATION_ON_WITHDRAWN = "integration.marketplace_connections_disabled_on_withdrawn"
WITHDRAWN = "inventory.dealership_listings.withdrawn"
_AS24 = MarketplaceChannel.AUTOSCOUT24


@pytest.fixture()
def deleted_secrets(monkeypatch) -> list[tuple[uuid.UUID, str]]:
    """The secrets manager's deletes, recorded (no live project in tests)."""

    calls: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        secrets_backend, "delete_secret", lambda *, connection_id, slot: calls.append((connection_id, slot))
    )
    return calls


def _live_betrieb(db_session, provider, *, vins: list[str]):
    """A Betrieb with an AutoScout24 connection holding a password, and its
    listings published and delivered (TRANSMITTED)."""

    dealership = _dealership(db_session)
    connection = _make_connection(db_session, provider, tenant_id=dealership.id)
    db_session.add(
        IntegrationSecretRef(
            connection_id=connection.id, slot=SecretSlot.PASSWORD, secret_ref=f"infisical://{uuid.uuid4()}"
        )
    )
    connection.status = ConnectionStatus.CONNECTED
    db_session.commit()
    for vin in vins:
        item = _make_ready_item(db_session, dealership.id, vin=vin)
        publish(db_session, item=item, channel=_AS24, actor_id=uuid.uuid4())
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=dealership.id, channel=_AS24)
    return dealership, connection


def _offboarded_message(db_session, dealership) -> OutboxMessage:
    """The real producer's row (the offboarding writes it in its transaction)."""

    offboarding.publish_offboarded(db_session, dealership=dealership, offboarded_at=offboarding.utcnow())
    db_session.commit()
    return db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == offboarding.OFFBOARDED_EVENT_TYPE, OutboxMessage.aggregate_id == dealership.id
        )
    ).one()


def _vehicle_data_connection(db_session, dealership) -> IntegrationConnection:
    """A connection Stock never publishes through (the demo vehicle-data provider)."""

    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="Demo", auth_type="none",
        required_secret_slots=[], capability_codes=["ident_basis"],
    )
    db_session.add(provider)
    db_session.commit()
    connection = connection_service.create_connection(
        db_session, tenant_id=dealership.id,
        data=ConnectionCreate(provider_id=provider.id, display_name="Demo", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    connection.status = ConnectionStatus.CONNECTED
    db_session.commit()
    return connection


def _withdrawn_messages(db_session, dealership_id) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == WITHDRAWN, OutboxMessage.aggregate_id == dealership_id)
        )
    )


def _transport(engine, *names: str) -> InProcessTransport:
    from tests.test_consumer_redelivery_idempotency import _registered

    transport = InProcessTransport(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    for name, (event_type, handler) in _registered().items():
        if name in names:
            transport.register(event_type, consumer_name=name, handler=handler)
    return transport


def _states(db_session, tenant_id) -> list[PublishingState]:
    return list(db_session.scalars(select(StockItemPublishing.state).where(StockItemPublishing.tenant_id == tenant_id)))


def _audits(db_session, entity_id, action: str) -> int:
    return db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == entity_id, AuditEvent.action == action)
    )


def test_the_offboarded_fact_names_the_betrieb_and_the_date(db_session):
    dealership = _dealership(db_session)
    message = _offboarded_message(db_session, dealership)

    assert message.producer == "platform" and message.tenant_id == dealership.id
    assert message.aggregate_type == "dealership" and message.aggregate_id == dealership.id
    assert set(message.payload) == {"dealershipId", "offboardedAt"}
    assert message.payload["dealershipId"] == str(dealership.id)


def test_the_worker_registers_the_offboarding_consumers_on_their_facts():
    from app.worker import register_handlers
    from tests.test_consumer_redelivery_idempotency import _RecordingTransport

    transport = _RecordingTransport()
    register_handlers(transport)
    assert {name for name, _handler in transport._handlers[offboarding.OFFBOARDED_EVENT_TYPE]} == {
        INVENTORY, INTEGRATION
    }
    assert [name for name, _handler in transport._handlers[WITHDRAWN]] == [INTEGRATION_ON_WITHDRAWN]


def test_the_withdrawn_fact_names_the_betrieb_and_its_cause(db_session, engine, monkeypatch):
    """Stated also when there was nothing to withdraw: Integration waits for it."""

    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    dealership = _dealership(db_session)
    message = _offboarded_message(db_session, dealership)

    _transport(engine, INVENTORY).deliver(message)

    (withdrawn,) = _withdrawn_messages(db_session, dealership.id)
    assert withdrawn.producer == "inventory" and withdrawn.tenant_id == dealership.id
    assert withdrawn.aggregate_type == "dealership_listings" and withdrawn.aggregate_id == dealership.id
    assert withdrawn.payload == {"dealershipId": str(dealership.id), "listingsWithdrawn": 0}
    assert withdrawn.causation_id == message.id and withdrawn.correlation_id == message.correlation_id


def test_offboarding_withdraws_every_live_listing_and_disables_every_connection(
    db_session, engine, monkeypatch, deleted_secrets
):
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    provider = _make_provider(db_session)
    leaving, leaving_connection = _live_betrieb(db_session, provider, vins=["WVWZZZ1KZAW218101", "WVWZZZ1KZAW218102"])
    sister, sister_connection = _live_betrieb(db_session, provider, vins=["WVWZZZ1KZAW218103"])
    sent_before = len(adapter.transmitted)
    message = _offboarded_message(db_session, leaving)

    transport = _transport(engine, INVENTORY, INTEGRATION, INTEGRATION_ON_WITHDRAWN)
    transport.deliver(message)
    (withdrawn,) = _withdrawn_messages(db_session, leaving.id)
    transport.deliver(withdrawn)
    db_session.expire_all()

    # Stock: the marketplace was sent the Betrieb's empty feed, then its listings were unpublished.
    assert adapter.transmitted[sent_before:] == [[]]
    assert _states(db_session, leaving.id) == [PublishingState.NOT_PUBLISHED] * 2
    unpublished = db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "inventory.stock_item.unpublished", OutboxMessage.tenant_id == leaving.id)
    )
    assert unpublished == 2
    # Integration: disabled, the secret deleted in the secrets manager and its reference dropped.
    connection = db_session.get(IntegrationConnection, leaving_connection.id)
    assert connection.enabled is False and connection.status == ConnectionStatus.DISABLED
    assert connection.rotated_at is not None
    assert db_session.scalars(
        select(IntegrationSecretRef).where(IntegrationSecretRef.connection_id == leaving_connection.id)
    ).all() == []
    assert deleted_secrets == [(leaving_connection.id, "password")]
    assert _audits(db_session, leaving_connection.id, "disable") == 1
    # The sister Betrieb is untouched.
    assert _states(db_session, sister.id) == [PublishingState.PUBLISHED]
    kept = db_session.get(IntegrationConnection, sister_connection.id)
    assert kept.enabled is True and kept.status == ConnectionStatus.CONNECTED
    assert db_session.scalar(
        select(func.count()).select_from(IntegrationSecretRef).where(IntegrationSecretRef.connection_id == kept.id)
    ) == 1

    # A redelivery is a no-op for every consumer.
    sent = len(adapter.transmitted)
    transport.deliver(message)
    transport.deliver(withdrawn)
    db_session.expire_all()
    assert len(_withdrawn_messages(db_session, leaving.id)) == 1
    assert len(adapter.transmitted) == sent
    assert _audits(db_session, leaving_connection.id, "disable") == 1
    assert deleted_secrets == [(leaving_connection.id, "password")]


def test_stocks_connection_stays_enabled_until_stock_has_withdrawn(db_session, engine, monkeypatch, deleted_secrets):
    """Delivery is per consumer, at least once and unordered (events.md):
    Stock's first delivery fails (the marketplace refused the empty feed)
    while Integration's runs. The marketplace connection stays enabled, so the
    retry still withdraws the feed; it is disabled only on Stock's fact that
    it has. A connection Stock never publishes through goes at once."""

    provider = _make_provider(db_session)
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, marketplace = _live_betrieb(db_session, provider, vins=["WVWZZZ1KZAW218107"])
    vehicle_data = _vehicle_data_connection(db_session, leaving)
    message = _offboarded_message(db_session, leaving)
    transport = _transport(engine, INVENTORY, INTEGRATION, INTEGRATION_ON_WITHDRAWN)

    _register_fake(monkeypatch, FakeMarketplaceAdapter(to_error=True))
    with pytest.raises(DeliveryError) as failed:
        transport.deliver(message)
    db_session.expire_all()

    assert [name for name, _exc in failed.value.failures] == [INVENTORY]
    assert _states(db_session, leaving.id) == [PublishingState.PUBLISHED]
    kept = db_session.get(IntegrationConnection, marketplace.id)
    assert kept.enabled is True and kept.status == ConnectionStatus.CONNECTED
    assert deleted_secrets == []
    assert db_session.get(IntegrationConnection, vehicle_data.id).enabled is False
    assert _withdrawn_messages(db_session, leaving.id) == []

    # The outbox's retry: only Stock is still missing its processed-event row.
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    transport.deliver(message)
    db_session.expire_all()

    assert adapter.transmitted == [[]]
    assert _states(db_session, leaving.id) == [PublishingState.NOT_PUBLISHED]
    assert db_session.get(IntegrationConnection, marketplace.id).enabled is True
    (withdrawn,) = _withdrawn_messages(db_session, leaving.id)

    transport.deliver(withdrawn)
    db_session.expire_all()

    connection = db_session.get(IntegrationConnection, marketplace.id)
    assert connection.enabled is False and connection.status == ConnectionStatus.DISABLED
    assert deleted_secrets == [(marketplace.id, "password")]


def test_a_betrieb_without_live_listings_or_connections_is_a_no_op(db_session, engine, monkeypatch, deleted_secrets):
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    dealership = _dealership(db_session)

    transport = _transport(engine, INVENTORY, INTEGRATION, INTEGRATION_ON_WITHDRAWN)
    transport.deliver(_offboarded_message(db_session, dealership))
    (withdrawn,) = _withdrawn_messages(db_session, dealership.id)
    transport.deliver(withdrawn)

    assert adapter.transmitted == [] and deleted_secrets == []


def test_a_failed_delivery_leaves_the_listings_live_for_the_retry(db_session, engine, monkeypatch):
    provider = _make_provider(db_session)
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, _connection = _live_betrieb(db_session, provider, vins=["WVWZZZ1KZAW218104"])
    _register_fake(monkeypatch, FakeMarketplaceAdapter(to_error=True))

    with pytest.raises(DeliveryError):
        _transport(engine, INVENTORY).deliver(_offboarded_message(db_session, leaving))
    db_session.expire_all()

    assert _states(db_session, leaving.id) == [PublishingState.PUBLISHED]
    assert _withdrawn_messages(db_session, leaving.id) == []


def test_a_delivered_feed_with_no_connection_left_to_withdraw_it_is_loud(db_session, monkeypatch):
    """The dealer had disabled the marketplace connection: the marketplace
    still holds the feed and nothing can withdraw it — raised, with no
    withdrawn fact (so Integration keeps the connection's credentials), and
    the outbox retries and finally dead-letters (rule 10), never silent."""

    provider = _make_provider(db_session)
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, connection = _live_betrieb(db_session, provider, vins=["WVWZZZ1KZAW218105"])
    rows = db_session.scalars(select(StockItemPublishing).where(StockItemPublishing.tenant_id == leaving.id)).all()
    assert [row.transmission_status for row in rows] == [TransmissionStatus.TRANSMITTED]
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())

    with pytest.raises(ListingWithdrawalBlocked, match="autoscout24"):
        withdraw_live_listings(db_session, tenant_id=leaving.id, causation_id=uuid.uuid4(), correlation_id=uuid.uuid4())
    db_session.rollback()
    assert _withdrawn_messages(db_session, leaving.id) == []


def test_a_listing_never_delivered_is_unpublished_without_a_feed(db_session, monkeypatch):
    """No connection ever (Carmarket and Autolina have none, KAN-72): nothing
    reached a marketplace, so the listing is simply unpublished."""

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    dealership = _dealership(db_session)
    item = _make_ready_item(db_session, dealership.id, vin="WVWZZZ1KZAW218106")
    publish(db_session, item=item, channel=_AS24, actor_id=uuid.uuid4())

    assert (
        withdraw_live_listings(
            db_session, tenant_id=dealership.id, causation_id=uuid.uuid4(), correlation_id=uuid.uuid4()
        )
        == 1
    )
    db_session.commit()

    assert _states(db_session, dealership.id) == [PublishingState.NOT_PUBLISHED]
    assert adapter.transmitted == []
