"""KAN-718: an unpublish whose empty feed never reaches the marketplace is
recorded as not delivered, reported, and blocks offboarding's withdrawal —
never a silent success.

`stock_item_publishing.at_marketplace` records what the last feed the
marketplace accepted holds (full delivery: what a later delivered feed omits
is deleted there). An unpublish whose feed cannot go out — no enabled
connection, a failed delivery — leaves its row FAILED with the reason and
still `at_marketplace`; inventory's nightly reconciliation names it an hour
later; offboarding's withdrawal sends that channel its empty feed, or, with
no connection left, refuses (no withdrawn fact). The second case of the
ticket: a listing whose later deliveries all failed (FAILED) while an earlier
feed is still live is judged by what the marketplace holds, not by its last
status.

On Postgres.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationAlarm
from app.integration.services import connections as connection_service
from app.inventory import reconciliation as inventory_reconciliation
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import PublishingState, StockItemPublishing, TransmissionStatus
from app.inventory.services import marketplace_transmission
from app.inventory.services.listing_withdrawal import ListingWithdrawalBlocked, withdraw_live_listings
from app.inventory.services.publishing import record_unpublished
from tests.test_inventory_marketplace_transmission import FakeMarketplaceAdapter, _make_provider, _register_fake
from tests.test_kan218_offboarding_withdraws_listings_and_connections import _AS24, _live_betrieb, _withdrawn_messages

STILL_LIVE = "stock_item_publishing unpublished but still held by the marketplace's last delivered feed"


def _row(db_session, tenant_id) -> StockItemPublishing:
    db_session.expire_all()
    (row,) = db_session.scalars(select(StockItemPublishing).where(StockItemPublishing.tenant_id == tenant_id)).all()
    return row


def _unpublish_and_transmit(db_session, tenant_id) -> None:
    """The dealer's unpublish, then its transmission consumer's run."""

    row = _row(db_session, tenant_id)
    record_unpublished(db_session, item=db_session.get(StockItem, row.stock_item_id), channel=_AS24, actor_id=uuid.uuid4())
    db_session.commit()
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=tenant_id, channel=_AS24)


def _findings(db_session, *, age: dt.timedelta = dt.timedelta(hours=2)) -> set[tuple[str, uuid.UUID]]:
    db_session.execute(update(StockItemPublishing).values(updated_at=utcnow() - age))
    db_session.commit()
    try:
        inventory_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return {(orphan.check_label, orphan.source_row_id) for orphan in alarm.orphans if orphan.check_label == STILL_LIVE}
    return set()


def _withdraw(db_session, tenant_id) -> int:
    return withdraw_live_listings(db_session, tenant_id=tenant_id, causation_id=uuid.uuid4(), correlation_id=uuid.uuid4())


def test_a_delivered_feed_marks_its_listings_at_the_marketplace(db_session, monkeypatch):
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, _ = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718001"])

    row = _row(db_session, leaving.id)
    assert (row.transmission_status, row.at_marketplace) == (TransmissionStatus.TRANSMITTED, True)


def test_an_unpublish_with_the_connection_disabled_is_recorded_as_not_delivered(db_session, monkeypatch):
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    leaving, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718002"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    sent_before = len(adapter.transmitted)

    _unpublish_and_transmit(db_session, leaving.id)

    row = _row(db_session, leaving.id)
    assert row.state == PublishingState.NOT_PUBLISHED
    assert row.transmission_status == TransmissionStatus.FAILED  # never the publish's old TRANSMITTED
    assert "No autoscout24 connection" in (row.last_transmission_error or "")
    assert row.at_marketplace is True
    assert adapter.transmitted[sent_before:] == []


def test_reconciliation_reports_an_unpublished_listing_whose_feed_failed_at_once(db_session, monkeypatch):
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718003"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    _unpublish_and_transmit(db_session, leaving.id)
    row_id = _row(db_session, leaving.id).id

    # A failed attempt is a finding at once: each later failed attempt moves
    # updated_at, so a grace on it could hide the row for good.
    assert _findings(db_session, age=dt.timedelta(0)) == {(STILL_LIVE, row_id)}


def test_reconciliation_reports_an_unpublish_never_attempted_only_after_an_hour(db_session, monkeypatch):
    """Unpublished, its transmission consumer not run yet (in flight)."""

    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, _connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718007"])
    row = _row(db_session, leaving.id)
    record_unpublished(db_session, item=db_session.get(StockItem, row.stock_item_id), channel=_AS24, actor_id=uuid.uuid4())
    db_session.commit()

    assert _findings(db_session, age=dt.timedelta(0)) == set()
    assert _findings(db_session) == {(STILL_LIVE, row.id)}


def test_offboarding_refuses_while_the_undelivered_unpublish_is_live_and_no_connection_can_withdraw_it(
    db_session, monkeypatch
):
    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718004"])
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    _unpublish_and_transmit(db_session, leaving.id)

    with pytest.raises(ListingWithdrawalBlocked, match="autoscout24"):
        _withdraw(db_session, leaving.id)
    db_session.rollback()
    assert _withdrawn_messages(db_session, leaving.id) == []


def test_a_clean_unpublish_clears_the_listing_and_nothing_is_reported_or_blocked(db_session, monkeypatch):
    adapter = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, adapter)
    leaving, connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718005"])

    _unpublish_and_transmit(db_session, leaving.id)

    row = _row(db_session, leaving.id)
    assert (row.transmission_status, row.at_marketplace) == (TransmissionStatus.TRANSMITTED, False)
    assert adapter.transmitted[-1] == []
    assert _findings(db_session) == set()
    # Disabled afterwards: nothing of the Betrieb is left at the marketplace.
    connection_service.disable_connection(db_session, connection=connection, actor_id=uuid.uuid4())
    assert _withdraw(db_session, leaving.id) == 0
    assert len(_withdrawn_messages(db_session, leaving.id)) == 1


def test_a_listing_whose_later_deliveries_all_failed_is_still_withdrawn(db_session, monkeypatch):
    """The ticket's second case: delivered once, then a failed full delivery
    (FAILED), then its unpublish's empty feed failed too — no row reads
    TRANSMITTED, yet the first feed is live. The withdrawal judges by what
    the marketplace holds and sends the empty feed."""

    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    leaving, _connection = _live_betrieb(db_session, _make_provider(db_session), vins=["WVWZZZ1KZAW718006"])
    _register_fake(monkeypatch, FakeMarketplaceAdapter(to_error=True))
    with pytest.raises(Exception):  # noqa: B017 - the delivery error the outbox would retry
        marketplace_transmission.assemble_and_transmit(db_session, tenant_id=leaving.id, channel=_AS24)
    with pytest.raises(Exception):  # noqa: B017
        _unpublish_and_transmit(db_session, leaving.id)
    row = _row(db_session, leaving.id)
    assert (row.state, row.transmission_status, row.at_marketplace) == (
        PublishingState.NOT_PUBLISHED,
        TransmissionStatus.FAILED,
        True,
    )

    working = FakeMarketplaceAdapter()
    _register_fake(monkeypatch, working)
    assert _withdraw(db_session, leaving.id) == 0
    db_session.commit()

    assert working.transmitted == [[]]
    assert _row(db_session, leaving.id).at_marketplace is False
    assert len(_withdrawn_messages(db_session, leaving.id)) == 1
