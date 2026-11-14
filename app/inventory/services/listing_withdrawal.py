"""KAN-218 (Anto, 2026-11-14, Q3 A): offboarding withdraws every live
marketplace listing of the Betrieb — no buyer contacts a closed business.

Platform states the fact (`platform.dealership.offboarded`, in the
offboarding transaction) and calls nothing here: it is the lowest layer
(ADR-131), and a write across two contexts is never one transaction (rule 12,
ADR-047). Stock's consumer withdraws on its own session; the consumer harness
commits it with the processed-event row, so a redelivery is a no-op, and a
listing already withdrawn (or never live) is simply not found.

Per channel: first the marketplace, then the rows. An empty feed goes through
the gateway (`marketplace_transmission.withdraw_feed`, full delivery: what a
feed omits is deleted there); only then is each listing unpublished through
the dealer's own unpublish write (`publishing.record_unpublished`: the state
and the `inventory.stock_item.unpublished` fact, whose transmission consumer
later finds nothing left to send). A failed delivery raises before any row
changes, so the retry finds the listings still live and delivers again.

When every channel is done, Stock states the fact
`inventory.dealership_listings.withdrawn` (`WITHDRAWN_EVENT_TYPE`) in the same
transaction — always, also when there was nothing to withdraw. The empty feed
needs the marketplace connection, and Integration disables the connections
Stock publishes through only on that fact (every other one on the offboarded
fact itself): delivery is per consumer, at least once and unordered, so an
order of registrations would not hold once Stock's first delivery failed.
Should the withdrawal still find no enabled connection while a listing was
delivered to the marketplace (`TRANSMITTED`) — the dealer had disabled it —
it raises rather than pretend, and states no fact: the outbox retries, then
dead-letters and alerts (rule 10), naming no secret, and the connection keeps
its credentials for the repair. A listing never delivered (no connection
ever, or never transmitted) is unpublished without a feed. The empty feed goes
to every channel the Betrieb ever published on, live rows or not: an
unpublish whose own feed never reached the marketplace is withdrawn too.

Staging runs no worker (ADR-081): there the listings stay live until a
worker runs.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import (
    MarketplaceChannel,
    PublishingState,
    StockItemPublishing,
    TransmissionStatus,
)
from app.inventory.services import marketplace_transmission
from app.inventory.services.publishing import record_unpublished

# KAN-218: payload dealershipId, listingsWithdrawn; the cause is the envelope's
# causationId (the offboarded fact) and its correlationId.
WITHDRAWN_EVENT_TYPE = "inventory.dealership_listings.withdrawn"


class ListingWithdrawalBlocked(RuntimeError):
    """A channel still holds a delivered feed of the Betrieb and no enabled
    connection can withdraw it."""


def withdraw_live_listings(
    db: Session, *, tenant_id: uuid.UUID, causation_id: uuid.UUID, correlation_id: uuid.UUID
) -> int:
    """Every live listing of `tenant_id` unpublished, its marketplaces sent
    an empty feed first, then the withdrawn fact caused by `causation_id`.
    Returns the listings withdrawn. Never commits."""

    rows = list(
        db.scalars(
            select(StockItemPublishing)
            .where(StockItemPublishing.tenant_id == tenant_id, StockItemPublishing.state == PublishingState.PUBLISHED)
            .order_by(StockItemPublishing.id)
            .with_for_update()
        ).all()
    )
    by_channel: dict[MarketplaceChannel, list[StockItemPublishing]] = defaultdict(list)
    for row in rows:
        by_channel[row.channel].append(row)
    items = {
        item.id: item
        for item in db.scalars(select(StockItem).where(StockItem.id.in_({row.stock_item_id for row in rows})))
    }

    # Every channel the Betrieb ever published on, not only the ones with a
    # live row: a listing unpublished here whose own empty feed never reached
    # the marketplace (failed, dead-lettered) is still live there, and once
    # the withdrawn fact disables the connection no replay could reach it.
    # The empty feed is idempotent under full delivery.
    channels = set(by_channel) | set(
        db.scalars(
            select(StockItemPublishing.channel).where(StockItemPublishing.tenant_id == tenant_id).distinct()
        ).all()
    )

    blocked: list[str] = []
    withdrawn = 0
    for channel in sorted(channels, key=lambda c: c.value):
        channel_rows = by_channel.get(channel, [])
        delivered = marketplace_transmission.withdraw_feed(db, tenant_id=tenant_id, channel=channel)
        if not delivered and any(row.transmission_status == TransmissionStatus.TRANSMITTED for row in channel_rows):
            blocked.append(channel.value)
            continue
        for row in channel_rows:
            record_unpublished(db, item=items[row.stock_item_id], channel=channel, actor_id=None)
            withdrawn += 1
    if blocked:
        raise ListingWithdrawalBlocked(
            f"Betrieb {tenant_id}: no enabled connection to withdraw its delivered feed on {', '.join(blocked)}."
        )
    publish_event(
        db,
        OutboxEvent(
            event_type=WITHDRAWN_EVENT_TYPE,
            tenant_id=tenant_id,
            producer="inventory",
            aggregate_type="dealership_listings",
            aggregate_id=tenant_id,
            payload={"dealershipId": str(tenant_id), "listingsWithdrawn": withdrawn},
            causation_id=causation_id,
            correlation_id=correlation_id,
        ),
    )
    return withdrawn
