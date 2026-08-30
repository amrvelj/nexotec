"""Marketplace publishing (WP-7 PR-8, ADR-062). Blocking conditions are
computed and NAMED BEFORE SEND using the marketplace's own field name —
`compute_blocking_conditions` returns (field, message) pairs, never a
generic "cannot publish" — confirmed verbatim against the live reference
prototype's own banner text ("Lagerstatus — Eine Werksbestellung steht
noch nicht am Lager.").

Unpublish is a confirmed destructive action (AS24i is a full-delivery
interface — an object no longer transmitted is DELETED at the
marketplace, statistics and URL included), never a plain toggle.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.models.stock_item_publishing import (
    MAX_ADDITIONAL_MEDIA,
    MarketplaceChannel,
    PublishingState,
    StockItemMedia,
    StockItemPublishing,
)
from app.inventory.schemas.publishing import BlockingCondition, ListingTextUpdate
from app.vehicle.public import has_current_energy_rating

_EVENT_PRODUCER = "inventory"
_NEW_CAR_ENERGY_LABEL_ODOMETER_THRESHOLD_KM = 2000


def compute_blocking_conditions(db: Session, item: StockItem) -> list[BlockingCondition]:
    conditions: list[BlockingCondition] = []

    if item.lifecycle_status == LifecycleStatus.PIPELINE:
        conditions.append(
            BlockingCondition(field="Lagerstatus", message="Eine Werksbestellung steht noch nicht am Lager.")
        )

    media_count = db.scalar(select(StockItemMedia.id).where(StockItemMedia.stock_item_id == item.id).limit(1))
    if media_count is None:
        conditions.append(BlockingCondition(field="Bilder", message="Keine eigenen Fotografien vorhanden."))

    if item.effective_price is None:
        conditions.append(BlockingCondition(field="Preis", message="Kein Effektivpreis hinterlegt."))

    if (
        item.condition == StockItemCondition.NEW
        and item.odometer_km is not None
        and item.odometer_km < _NEW_CAR_ENERGY_LABEL_ODOMETER_THRESHOLD_KM
        and item.vehicle_id is not None
        and not has_current_energy_rating(db, item.vehicle_id)
    ):
        conditions.append(
            BlockingCondition(field="Energieetikette", message="Energieetikette fehlt für Neuwagen unter 2000 km.")
        )

    return conditions


def get_or_create_publishing(db: Session, item: StockItem, channel: MarketplaceChannel) -> StockItemPublishing:
    existing = db.scalar(
        select(StockItemPublishing).where(
            StockItemPublishing.stock_item_id == item.id, StockItemPublishing.channel == channel
        )
    )
    if existing is not None:
        return existing
    row = StockItemPublishing(tenant_id=item.tenant_id, stock_item_id=item.id, channel=channel)
    db.add(row)
    db.flush()
    return row


def update_listing_text(
    db: Session, *, item: StockItem, channel: MarketplaceChannel, data: ListingTextUpdate, actor_id: uuid.UUID | None
) -> StockItemPublishing:
    row = get_or_create_publishing(db, item, channel)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_by = actor_id
    row.version += 1
    db.commit()
    db.refresh(row)
    return row


def publish(db: Session, *, item: StockItem, channel: MarketplaceChannel, actor_id: uuid.UUID | None) -> StockItemPublishing:
    conditions = compute_blocking_conditions(db, item)
    if conditions:
        raise ConflictError(
            "Publication is blocked.",
            details={"blockingConditions": [c.model_dump(by_alias=True) for c in conditions]},
        )

    row = get_or_create_publishing(db, item, channel)
    row.state = PublishingState.PUBLISHED
    row.last_published_at = utcnow()
    row.updated_by = actor_id
    row.version += 1
    db.flush()

    publish_event(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.published",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"channel": channel.value},
        ),
    )
    db.commit()
    db.refresh(row)
    return row


def unpublish(
    db: Session, *, item: StockItem, channel: MarketplaceChannel, confirm: bool, actor_id: uuid.UUID | None
) -> StockItemPublishing:
    """AS24i's own full-delivery semantics mean this DELETES the listing,
    its statistics and its URL at the marketplace — a confirmed
    destructive action, never a plain toggle."""

    if not confirm:
        raise BadRequestError("Unpublishing is a confirmed destructive action — resend with confirm=true.")

    row = get_or_create_publishing(db, item, channel)
    row.state = PublishingState.NOT_PUBLISHED
    row.updated_by = actor_id
    row.version += 1
    db.commit()
    db.refresh(row)
    return row


def list_media(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> list[StockItemMedia]:
    return list(
        db.scalars(
            select(StockItemMedia)
            .where(StockItemMedia.tenant_id == tenant_id, StockItemMedia.stock_item_id == stock_item_id)
            .order_by(StockItemMedia.position.asc())
        ).all()
    )


def add_media(db: Session, *, item: StockItem, url: str) -> StockItemMedia:
    existing = list_media(db, tenant_id=item.tenant_id, stock_item_id=item.id)
    if len(existing) >= 1 + MAX_ADDITIONAL_MEDIA:
        raise ConflictError(
            f"A stock item may carry at most {1 + MAX_ADDITIONAL_MEDIA} images (1 main + {MAX_ADDITIONAL_MEDIA} additional).",
            details={"stockItemId": str(item.id)},
        )
    next_position = len(existing) + 1
    media = StockItemMedia(tenant_id=item.tenant_id, stock_item_id=item.id, position=next_position, url=url)
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


def reorder_media(db: Session, *, item: StockItem, ordered_media_ids: list[uuid.UUID]) -> list[StockItemMedia]:
    """Picture order IS the product — main image is simply position 1.
    Reassigns 1..N in the given order; every existing media row must be
    named exactly once."""

    existing = {m.id: m for m in list_media(db, tenant_id=item.tenant_id, stock_item_id=item.id)}
    if set(existing.keys()) != set(ordered_media_ids):
        raise BadRequestError("ordered_media_ids must name every existing image exactly once.")

    # Two-pass reassignment avoids a transient unique(stock_item_id,
    # position) collision when the new order isn't a pure rotation.
    for media in existing.values():
        media.position += 1000
    db.flush()
    for new_position, media_id in enumerate(ordered_media_ids, start=1):
        existing[media_id].position = new_position
    db.commit()
    return list_media(db, tenant_id=item.tenant_id, stock_item_id=item.id)


def remove_media(db: Session, *, item: StockItem, media_id: uuid.UUID) -> None:
    media = db.get(StockItemMedia, media_id)
    if media is None or media.stock_item_id != item.id:
        return
    removed_position = media.position
    db.delete(media)
    db.flush()
    # Close the gap so positions stay a dense 1..N sequence.
    for m in list_media(db, tenant_id=item.tenant_id, stock_item_id=item.id):
        if m.position > removed_position:
            m.position -= 1
    db.commit()
