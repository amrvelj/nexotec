"""A stock item on the Betrieb's own website (KAN-225; D-A-26, FR-A-05,
FR-A-27).

Rules held here:
- **Refused before it exists (FR-A-05):** listing a car on the website needs
  what any listing needs (`listing_conditions.compute_blocking_conditions`: a car in
  stock, photos, a price, …), a car that has not left stock, and a
  publishing profile the website channel accepts
  (`platform.public.publishing_profile_missing_items`). Each refusal names
  its item; the profile's carry a stable `code` the SPA renders in the
  dealer's words.
- **Stated, never transmitted:** the website reads only the public stock
  projection (`app/reporting`), built from
  `inventory.stock_item.website_listing_changed` — written here, with the
  listing's change, in the same transaction (rule 4). Its `listing` is
  `public_snapshot` — the allow-list `WEBSITE_LISTING_FIELDS`: never cost,
  margin, purchase price, valuation or an internal note (ADR-029).
- **Kept current:** every write path that changes a field of the snapshot
  calls `restate_if_listed` after its change (the item PATCH, the pricing
  dialog, «Konfiguration zuordnen», the label refresh, the condition, the
  photos, a reservation and its release, the car leaving stock). A change
  nothing public sees states nothing (`snapshot_digest`); a car that left
  stock is taken off the website by the same call.
- `revision` rises with each statement, so the projection applies only a
  newer one (delivery is unordered).
"""

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import ReservationState, StockItem
from app.inventory.models.stock_item_publishing import StockItemMedia
from app.inventory.models.website_listing import StockItemWebsiteListing
from app.inventory.schemas.publishing import BlockingCondition
from app.inventory.services.listing_conditions import compute_blocking_conditions
from app.platform.public import publishing_profile_missing_items

EVENT_TYPE = "inventory.stock_item.website_listing_changed"
_CENTS = Decimal("0.01")
_EVENT_PRODUCER = "inventory"

# The only fields of a stock item the public may see (ADR-029). A field is
# added here on purpose, never by a snapshot that grew: the reporting
# projection keeps exactly the same list and drops anything else.
WEBSITE_LISTING_FIELDS = (
    "stockNumber",
    "vehicleLabel",
    "condition",
    "firstRegistrationDate",
    "odometerKm",
    "exteriorColour",
    "bodyStyle",
    "priceChf",
    "reserved",
    "imageUrls",
)


def get_listing(db: Session, item: StockItem, *, for_update: bool = False) -> StockItemWebsiteListing | None:
    stmt = select(StockItemWebsiteListing).where(
        StockItemWebsiteListing.tenant_id == item.tenant_id, StockItemWebsiteListing.stock_item_id == item.id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def read_listing(db: Session, item: StockItem) -> StockItemWebsiteListing:
    """The listing to READ: stored, or a transient unlisted one at version 1
    (the version its first write creates it with). A read never writes."""

    row = get_listing(db, item)
    if row is not None:
        return row
    transient = StockItemWebsiteListing(tenant_id=item.tenant_id, stock_item_id=item.id, is_listed=False, revision=0)
    transient.version = 1
    return transient


def public_snapshot(db: Session, item: StockItem) -> dict[str, Any]:
    media = db.scalars(
        select(StockItemMedia.url)
        .where(StockItemMedia.tenant_id == item.tenant_id, StockItemMedia.stock_item_id == item.id)
        .order_by(StockItemMedia.position)
    ).all()
    snapshot = {
        "stockNumber": item.stock_number,
        "vehicleLabel": item.vehicle_label,
        "condition": item.condition.value,
        "firstRegistrationDate": item.first_registration_date.isoformat() if item.first_registration_date else None,
        "odometerKm": item.odometer_km,
        "exteriorColour": item.exterior_colour,
        "bodyStyle": item.body_style,
        "priceChf": None if item.effective_price is None else str(item.effective_price.quantize(_CENTS)),
        "reserved": item.reservation_state == ReservationState.RESERVED,
        "imageUrls": list(media),
    }
    assert tuple(snapshot) == WEBSITE_LISTING_FIELDS
    return snapshot


def _digest(snapshot: dict[str, Any] | None, location_id: uuid.UUID | None) -> str:
    raw = json.dumps({"listing": snapshot, "locationId": str(location_id) if location_id else None}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _state(db: Session, item: StockItem, row: StockItemWebsiteListing) -> None:
    snapshot = public_snapshot(db, item) if row.is_listed else None
    row.revision += 1
    row.snapshot_digest = _digest(snapshot, item.location_id) if row.is_listed else None
    db.flush()
    publish(
        db,
        OutboxEvent(
            event_type=EVENT_TYPE,
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={
                "stockItemId": str(item.id),
                "listed": row.is_listed,
                "revision": row.revision,
                "locationId": str(item.location_id) if item.location_id else None,
                "listedAt": row.listed_at.isoformat() if row.listed_at and row.is_listed else None,
                "listing": snapshot,
            },
        ),
    )


def blocking_conditions(db: Session, item: StockItem) -> list[BlockingCondition]:
    conditions = list(compute_blocking_conditions(db, item))
    if item.left_stock_at is not None:
        conditions.append(BlockingCondition(field="Lagerstatus", message="left_stock", code="vehicle_left_stock"))
    for missing in publishing_profile_missing_items(db, dealership_id=item.tenant_id, channel="website"):
        conditions.append(
            BlockingCondition(
                field="publishingProfile",
                message=missing.location_name or "",
                code=missing.code,
            )
        )
    return conditions


def list_on_website(db: Session, *, item: StockItem, actor_id: uuid.UUID | None) -> StockItemWebsiteListing:
    """The caller holds the item's row lock and has checked If-Match against
    `read_listing(...).version`."""

    conditions = blocking_conditions(db, item)
    if conditions:
        raise ConflictError(
            "Publication is blocked.",
            details={"blockingConditions": [c.model_dump(by_alias=True) for c in conditions]},
        )
    row = get_listing(db, item, for_update=True)
    if row is None:
        row = StockItemWebsiteListing(tenant_id=item.tenant_id, stock_item_id=item.id, is_listed=False, revision=0)
        row.version = 1
        db.add(row)
    if not row.is_listed:
        row.is_listed = True
        row.listed_at = utcnow()
    row.updated_by = actor_id
    row.version += 1
    _state(db, item, row)
    db.commit()
    db.refresh(row)
    return row


def unlist_from_website(db: Session, *, item: StockItem, actor_id: uuid.UUID | None) -> StockItemWebsiteListing:
    row = get_listing(db, item, for_update=True)
    if row is None or not row.is_listed:
        if row is None:
            return read_listing(db, item)
        return row
    _unlist(db, item, row, actor_id)
    db.commit()
    db.refresh(row)
    return row


def _unlist(db: Session, item: StockItem, row: StockItemWebsiteListing, actor_id: uuid.UUID | None) -> None:
    row.is_listed = False
    row.unlisted_at = utcnow()
    row.updated_by = actor_id
    row.version += 1
    _state(db, item, row)


def restate_if_listed(db: Session, item: StockItem) -> None:
    """After a write that may have changed a public field, in its
    transaction (never commits): states the new snapshot when the car is on
    the website and something public changed; takes it off when it left
    stock."""

    row = get_listing(db, item, for_update=True)
    if row is None or not row.is_listed:
        return
    db.flush()
    if item.left_stock_at is not None:
        _unlist(db, item, row, None)
        return
    if _digest(public_snapshot(db, item), item.location_id) == row.snapshot_digest:
        return
    _state(db, item, row)


def withdraw_all(db: Session, *, tenant_id: uuid.UUID) -> int:
    """Every car of the Betrieb off its website (offboarding). Never commits."""

    rows = list(
        db.scalars(
            select(StockItemWebsiteListing)
            .where(StockItemWebsiteListing.tenant_id == tenant_id, StockItemWebsiteListing.is_listed.is_(True))
            .order_by(StockItemWebsiteListing.id)
            .with_for_update()
        ).all()
    )
    items = {i.id: i for i in db.scalars(select(StockItem).where(StockItem.id.in_({r.stock_item_id for r in rows})))}
    for row in rows:
        _unlist(db, items[row.stock_item_id], row, None)
    return len(rows)


def count_listed(db: Session, *, tenant_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count())
        .select_from(StockItemWebsiteListing)
        .where(StockItemWebsiteListing.tenant_id == tenant_id, StockItemWebsiteListing.is_listed.is_(True))
    ) or 0
