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

The empty feed needs the connection, which Integration's own consumer of the
same fact disables. `app/worker.py` registers Stock's consumer first, and the
in-process transport delivers a message to its consumers in that order. Should
the withdrawal still find no enabled connection while a listing was delivered
to the marketplace (`TRANSMITTED`) — Stock failed on the first delivery and
Integration did not — it raises rather than pretend: the outbox retries, then
dead-letters and alerts (rule 10), naming no secret. A listing never delivered
(no connection ever, or never transmitted) is unpublished without a feed.

Staging runs no worker (ADR-081): there the listings stay live until a
worker runs.
"""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import (
    MarketplaceChannel,
    PublishingState,
    StockItemPublishing,
    TransmissionStatus,
)
from app.inventory.services import marketplace_transmission
from app.inventory.services.publishing import record_unpublished


class ListingWithdrawalBlocked(RuntimeError):
    """A channel still holds a delivered feed of the Betrieb and no enabled
    connection can withdraw it."""


def withdraw_live_listings(db: Session, *, tenant_id: uuid.UUID) -> int:
    """Every live listing of `tenant_id` unpublished, its marketplaces sent
    an empty feed first. Returns the listings withdrawn. Never commits."""

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

    blocked: list[str] = []
    withdrawn = 0
    for channel in sorted(by_channel, key=lambda c: c.value):
        channel_rows = by_channel[channel]
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
    return withdrawn
