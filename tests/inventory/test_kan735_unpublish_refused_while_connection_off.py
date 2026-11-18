"""KAN-735 (Anto, 2026-11-15; ADR-062 as amended): the dealer's unpublish is
refused while the marketplace still holds the listing and the Betrieb has no
enabled connection for the channel — the car would stay live there. A
listing never delivered has nothing to withdraw and is unpublished. The
automatic path (offboarding's withdrawal) is not refused: it keeps KAN-718's
behaviour.

On Postgres.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.integration.services import connections as connection_service
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import PublishingState, StockItemPublishing, TransmissionStatus
from app.inventory.services import marketplace_transmission
from app.inventory.services.listing_withdrawal import ListingWithdrawalBlocked, withdraw_live_listings
from app.inventory.services.publishing import publish, record_unpublished, unpublish
from tests.cross_context.test_kan218_offboarding_withdraws_listings_and_connections import _AS24, _live_betrieb
from tests.inventory.test_inventory_marketplace_transmission import (
    FakeMarketplaceAdapter,
    _make_provider,
    _make_ready_item,
    _register_fake,
)


def _rows(db_session, tenant_id) -> list[StockItemPublishing]:
    db_session.expire_all()
    return list(db_session.scalars(select(StockItemPublishing).where(StockItemPublishing.tenant_id == tenant_id)).all())


def _unpublished_facts(db_session, item_id) -> int:
    return len(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "inventory.stock_item.unpublished", OutboxMessage.aggregate_id == item_id
            )
        ).all()
    )


def test_an_unpublish_of_a_live_listing_is_refused_while_the_connection_is_off(db_session, monkeypatch):
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    dealership, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW735001"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    (row,) = _rows(db_session, dealership.id)
    assert row.at_marketplace is True
    version = row.version
    item = db_session.get(StockItem, row.stock_item_id)

    with pytest.raises(ConflictError) as refused:
        unpublish(db_session, item=item, channel=_AS24, confirm=True, actor_id=uuid.uuid4())

    assert refused.value.details == {"reason": "marketplace_connection_disabled", "channel": "autoscout24"}
    db_session.rollback()
    (row,) = _rows(db_session, dealership.id)
    assert (row.state, row.version, row.at_marketplace) == (PublishingState.PUBLISHED, version, True)
    assert _unpublished_facts(db_session, item.id) == 0


def test_the_same_unpublish_goes_through_once_the_connection_is_on_again(db_session, monkeypatch):
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    dealership, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW735002"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    connection_service.enable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    (row,) = _rows(db_session, dealership.id)
    item = db_session.get(StockItem, row.stock_item_id)

    unpublish(db_session, item=item, channel=_AS24, confirm=True, actor_id=uuid.uuid4())
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=dealership.id, channel=_AS24)

    (row,) = _rows(db_session, dealership.id)
    assert (row.state, row.transmission_status, row.at_marketplace) == (
        PublishingState.NOT_PUBLISHED,
        TransmissionStatus.TRANSMITTED,
        False,
    )
    assert adapter.transmitted[-1] == []


def test_a_listing_never_delivered_is_unpublished_with_the_connection_off(db_session, monkeypatch):
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    dealership, connection = _live_betrieb(db_session, _make_provider(db_session), vins=[])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    item = _make_ready_item(db_session, dealership.id, vin="WVWZZZ1KZAW735003")
    publish(db_session, item=item, channel=_AS24, actor_id=uuid.uuid4())  # never transmitted: the connection is off
    (row,) = _rows(db_session, dealership.id)
    assert row.at_marketplace is False

    unpublish(db_session, item=item, channel=_AS24, confirm=True, actor_id=uuid.uuid4())

    (row,) = _rows(db_session, dealership.id)
    assert row.state == PublishingState.NOT_PUBLISHED
    assert _unpublished_facts(db_session, item.id) == 1


def test_offboardings_withdrawal_is_not_refused_and_keeps_kan_718s_behaviour(db_session, monkeypatch):
    """The automatic path records the unpublish (state and fact) and, with no
    connection left to send the empty feed, holds the offboarding — never the
    dealer-facing refusal."""

    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    dealership, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW735004"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    (row,) = _rows(db_session, dealership.id)
    item = db_session.get(StockItem, row.stock_item_id)

    # The write the automatic path shares is never refused, even for a live
    # listing with the connection off; it is recorded as not delivered.
    record_unpublished(db_session, item=item, channel=_AS24, actor_id=None)
    db_session.commit()
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=dealership.id, channel=_AS24)
    (row,) = _rows(db_session, dealership.id)
    assert (row.state, row.transmission_status, row.at_marketplace) == (
        PublishingState.NOT_PUBLISHED,
        TransmissionStatus.FAILED,
        True,
    )
    assert _unpublished_facts(db_session, item.id) == 1

    # And offboarding is held while that listing is live (KAN-718).
    with pytest.raises(ListingWithdrawalBlocked, match="autoscout24"):
        withdraw_live_listings(db_session, tenant_id=dealership.id, causation_id=uuid.uuid4(), correlation_id=uuid.uuid4())
