"""KAN-27 (WP-7, ADR-062) — the marketplace feed transmission service.

The property under test throughout is the one the ticket itself calls
out as dangerous: AS24i's full-delivery semantics mean an object missing
from a delivered file is DELETED at the marketplace, so a feed must never
be sent unless EVERY currently-published item built cleanly. A fake
adapter (never the real AutoScout24Adapter/FTP — that mapping has its own
coverage in test_integration_autoscout24_adapter.py) records exactly what
it was asked to transmit, so these tests assert on "was anything sent at
all, and what" rather than a specific wire format.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.uuid7 import uuid7
from app.integration.adapters.marketplace_base import MarketplaceListingError, MarketplaceTransmissionError
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway, resilience
from app.inventory.consumers import handle_stock_item_published_message, handle_stock_item_unpublished_message
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.models.stock_item_publishing import MarketplaceChannel, TransmissionStatus
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate
from app.inventory.services import marketplace_transmission
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.publishing import (
    add_media,
    compute_blocking_conditions,
    get_or_create_publishing,
    publish,
    unpublish,
)
from app.inventory.services.stock_item import create_stock_item, update_stock_item


def _make_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="autoscout24",
        category="marketplace",
        display_name="AutoScout24",
        auth_type="ftp_password",
        required_secret_slots=["password"],
        capability_codes=["marketplace_publish"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id):
    return connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="AutoScout24", environment=ConnectionEnvironment.PRODUCTION,
            config={"kundennummer": "EFAG-042"},
        ),
        actor_id=uuid.uuid4(),
    )


def _make_ready_item(db_session, tenant_id, *, vin):
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    item = promote_to_vehicle_mdm(db_session, item=item, vin=vin)
    item = update_stock_item(
        db_session, item=item,
        data=StockItemUpdate(
            effective_price=Decimal("19900.00"), exterior_colour="Blau metallic", body_style="Kombi",
            odometer_km=42000, first_registration_date=dt.date(2021, 3, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    add_media(db_session, item=item, url="https://example.com/photo1.jpg")
    return item


class FakeMarketplaceAdapter:
    """Records every listing it's asked to validate/transmit. `to_fail`
    names stock_item_ids whose `validate_listing` should raise; `to_error`
    makes `transmit_feed` itself raise (a delivery failure, not a data
    one)."""

    def __init__(self, *, to_fail: set[str] | None = None, to_error: bool = False):
        self._to_fail = to_fail or set()
        self._to_error = to_error
        self.transmitted: list[list] = []

    def validate_listing(self, listing):
        if listing.stock_item_id in self._to_fail:
            raise MarketplaceListingError(
                stock_item_id=listing.stock_item_id, field="Aussenfarbe", message="synthetic failure"
            )

    def transmit_feed(self, listings):
        if self._to_error:
            raise MarketplaceTransmissionError("FTP connection refused")
        self.transmitted.append(list(listings))
        from app.integration.adapters.marketplace_base import TransmissionResult

        return TransmissionResult(transmitted_count=len(listings))


def _register_fake(monkeypatch, adapter: FakeMarketplaceAdapter) -> None:
    monkeypatch.setattr(
        gateway, "_ADAPTER_FACTORIES",
        {**gateway._ADAPTER_FACTORIES, "autoscout24": lambda db, connection, actor_id, purpose: adapter},
    )


def test_publishing_two_items_transmits_both_and_marks_them_transmitted(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    item_a = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004352")
    item_b = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004353")
    publish(db_session, item=item_a, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=item_b, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    assert len(adapter.transmitted) == 1
    sent_ids = {listing.stock_item_id for listing in adapter.transmitted[0]}
    assert sent_ids == {str(item_a.id), str(item_b.id)}

    row_a = get_or_create_publishing(db_session, item_a, MarketplaceChannel.AUTOSCOUT24)
    row_b = get_or_create_publishing(db_session, item_b, MarketplaceChannel.AUTOSCOUT24)
    for row in (row_a, row_b):
        assert row.transmission_status == TransmissionStatus.TRANSMITTED
        assert row.last_transmission_error is None
        assert row.last_attempted_at is not None


def test_one_bad_listing_aborts_the_whole_feed_the_good_one_is_never_sent(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    good = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004354")
    bad = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004355")
    publish(db_session, item=good, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=bad, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter(to_fail={str(bad.id)})
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    # transmit_feed was NEVER called — a truncated set is never sent.
    assert adapter.transmitted == []

    good_row = get_or_create_publishing(db_session, good, MarketplaceChannel.AUTOSCOUT24)
    bad_row = get_or_create_publishing(db_session, bad, MarketplaceChannel.AUTOSCOUT24)
    # The good item is untouched — still PENDING, not marked FAILED and
    # not marked TRANSMITTED either, since nothing was actually sent.
    assert good_row.transmission_status == TransmissionStatus.PENDING
    assert bad_row.transmission_status == TransmissionStatus.FAILED
    assert "Aussenfarbe" in bad_row.last_transmission_error


def test_no_connection_configured_marks_every_published_item_failed(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    item_a = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004356")
    item_b = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004396")
    publish(db_session, item=item_a, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=item_b, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    for item in (item_a, item_b):
        row = get_or_create_publishing(db_session, item, MarketplaceChannel.AUTOSCOUT24)
        assert row.transmission_status == TransmissionStatus.FAILED
        assert "connection" in row.last_transmission_error.lower()


def test_carmarket_has_no_seeded_provider_and_fails_the_same_honest_way(db_session):
    # No IntegrationProvider row is ever seeded for carmarket (no
    # specification exists) — this must degrade exactly like "dealer
    # hasn't connected yet," never crash the consumer, and with the SAME
    # message shape, not an opaque or channel-specific-looking error.
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004357")
    publish(db_session, item=item, channel=MarketplaceChannel.CARMARKET, actor_id=uuid.uuid4())

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.CARMARKET)

    row = get_or_create_publishing(db_session, item, MarketplaceChannel.CARMARKET)
    assert row.transmission_status == TransmissionStatus.FAILED
    assert row.last_transmission_error == "No carmarket connection configured for this dealership."


def test_a_transmission_failure_marks_every_published_item_failed_not_just_one(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    item_a = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004358")
    item_b = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004359")
    publish(db_session, item=item_a, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=item_b, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter(to_error=True)
    _register_fake(monkeypatch, adapter)

    # A genuine transmission failure re-raises (see marketplace_transmission
    # .py's own comment) so the outbox's own retry/backoff/dead-letter
    # mechanism engages — it is NOT swallowed here. The rows are still
    # marked FAILED first, for visibility while a retry is pending.
    with pytest.raises(MarketplaceTransmissionError, match="FTP connection refused"):
        marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    for item in (item_a, item_b):
        row = get_or_create_publishing(db_session, item, MarketplaceChannel.AUTOSCOUT24)
        assert row.transmission_status == TransmissionStatus.FAILED
        assert "FTP connection refused" in row.last_transmission_error


def test_unpublishing_one_item_removes_it_from_the_next_feed(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    keep = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004360")
    drop = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004361")
    publish(db_session, item=keep, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=drop, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)
    assert {l.stock_item_id for l in adapter.transmitted[0]} == {str(keep.id), str(drop.id)}

    from app.inventory.services.publishing import unpublish

    unpublish(db_session, item=drop, channel=MarketplaceChannel.AUTOSCOUT24, confirm=True, actor_id=uuid.uuid4())
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    # The second feed carries ONLY the still-published item — `drop` is
    # simply absent, which is how AS24i's own full-delivery semantics
    # delete it at the marketplace. No separate "delete" call exists.
    assert len(adapter.transmitted) == 2
    assert {l.stock_item_id for l in adapter.transmitted[1]} == {str(keep.id)}


def test_replaying_the_publish_event_sends_the_same_feed_again_without_error(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004362")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    assert len(adapter.transmitted) == 2
    assert {l.stock_item_id for l in adapter.transmitted[0]} == {l.stock_item_id for l in adapter.transmitted[1]}


def test_the_published_consumer_handler_delegates_to_assemble_and_transmit(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004363")
    row = publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)

    message = OutboxMessage(
        id=uuid7(), event_type="inventory.stock_item.published", event_version=1, occurred_at=utcnow(),
        tenant_id=tenant_id, producer="inventory", aggregate_type="stock_item", aggregate_id=item.id,
        correlation_id=uuid7(), causation_id=None, payload={"channel": "autoscout24"},
        status=OutboxStatus.PENDING, attempts=0, next_attempt_at=utcnow(),
    )
    db_session.add(message)
    db_session.flush()

    handle_stock_item_published_message(db_session, message)

    assert len(adapter.transmitted) == 1
    db_session.refresh(row)
    assert row.transmission_status == TransmissionStatus.TRANSMITTED


def test_the_unpublished_consumer_handler_delegates_to_assemble_and_transmit(db_session, monkeypatch):
    # app/worker.py registers this handler for inventory.stock_item.
    # unpublished (the new event this PR adds) — nothing exercised it
    # before, since every other test calls assemble_and_transmit directly
    # rather than through the real consumer entry point.
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    _make_connection(db_session, provider, tenant_id=tenant_id)
    keep = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004364")
    drop = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004365")
    publish(db_session, item=keep, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    publish(db_session, item=drop, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    unpublish(db_session, item=drop, channel=MarketplaceChannel.AUTOSCOUT24, confirm=True, actor_id=uuid.uuid4())

    message = OutboxMessage(
        id=uuid7(), event_type="inventory.stock_item.unpublished", event_version=1, occurred_at=utcnow(),
        tenant_id=tenant_id, producer="inventory", aggregate_type="stock_item", aggregate_id=drop.id,
        correlation_id=uuid7(), causation_id=None, payload={"channel": "autoscout24"},
        status=OutboxStatus.PENDING, attempts=0, next_attempt_at=utcnow(),
    )
    db_session.add(message)
    db_session.flush()

    handle_stock_item_unpublished_message(db_session, message)

    assert len(adapter.transmitted) == 1
    assert {l.stock_item_id for l in adapter.transmitted[0]} == {str(keep.id)}


def test_unpublish_actually_emits_the_outbox_event_it_promises(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004366")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    unpublish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, confirm=True, actor_id=uuid.uuid4())

    message = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.unpublished",
            OutboxMessage.aggregate_id == item.id,
        )
    ).one()
    assert message.tenant_id == tenant_id
    assert message.payload == {"channel": "autoscout24"}


def test_a_validation_failure_never_touches_the_call_log_or_circuit_breaker(db_session, monkeypatch):
    # The central design fix this PR's review caught: validation happens
    # via resolve_adapter (no logging), never call_capability — so a
    # dealer's own missing field must never write a call-log row or
    # change the connection's circuit-breaker state at all, success or
    # failure. An earlier version of this code entered call_capability
    # for validation too and returned early on failure, which
    # call_capability's own contextmanager records as a SUCCESSFUL call —
    # silently resetting any real accumulated connection failures.
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    resilience.reset_circuit(connection.id)
    # Simulate a connection with real accumulated failures BEFORE this
    # run — if validation-refusal is wrongly treated as a success, this
    # count gets silently zeroed.
    resilience.record_failure(connection.id)
    resilience.record_failure(connection.id)

    bad = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004367")
    publish(db_session, item=bad, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter(to_fail={str(bad.id)})
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    assert adapter.transmitted == []
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 0
    circuit = resilience._CIRCUITS[connection.id]
    assert circuit.failure_count == 2, "a validation-only refusal must not touch the circuit breaker at all"


def test_a_successful_transmission_does_write_exactly_one_call_log_row(db_session, monkeypatch):
    # The other half of the same property: once every listing validates
    # and a real transmission is attempted, call_capability's own
    # logging must fire normally.
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004368")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)

    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=MarketplaceChannel.AUTOSCOUT24)

    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 1


# --- the four new blocking conditions (exterior_colour, body_style,
# odometer_km, first_registration_date-unless-new) --------------------------


def test_missing_exterior_colour_blocks_publish_with_the_marketplace_field_name(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004369")
    item.exterior_colour = None
    db_session.flush()
    conditions = compute_blocking_conditions(db_session, item)
    assert any(c.field == "Aussenfarbe" for c in conditions)


def test_missing_body_style_blocks_publish_with_the_marketplace_field_name(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004370")
    item.body_style = None
    db_session.flush()
    conditions = compute_blocking_conditions(db_session, item)
    assert any(c.field == "Aufbau" for c in conditions)


def test_missing_odometer_blocks_publish_with_the_marketplace_field_name(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004371")
    item.odometer_km = None
    db_session.flush()
    conditions = compute_blocking_conditions(db_session, item)
    assert any(c.field == "Kilometer" for c in conditions)


def test_missing_first_registration_blocks_publish_for_a_used_car(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004372")
    item.first_registration_date = None
    db_session.flush()
    conditions = compute_blocking_conditions(db_session, item)
    assert any(c.field == "InvSetzJahr" for c in conditions)


def test_missing_first_registration_does_not_block_a_new_car(db_session):
    # AS24i's own carve-out (p8): a NEW car may ship its model year
    # instead of a first-registration date.
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id, vin="1HGCM82633A004373")
    item.condition = StockItemCondition.NEW
    item.first_registration_date = None
    db_session.flush()
    conditions = compute_blocking_conditions(db_session, item)
    assert not any(c.field == "InvSetzJahr" for c in conditions)
