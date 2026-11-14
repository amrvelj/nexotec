"""The public stock projection (KAN-225, FR-A-27, ADR-029).

`apply_listing_changed` is the consumer's work: it applies Stock's statement
when it is newer than the row's (`revision`; delivery is at-least-once and
unordered — the harness's processed-event row makes a redelivery a no-op,
the revision makes an older statement one), keeping only the allow-listed
fields. `list_public_stock` is the read the website (and the publishing
screen) takes, one Betrieb at a time, optionally one location.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.reporting.models.public_stock_listing import PublicStockListing

# Exactly what the public may see of a stock item. Never cost, margin,
# purchase or landed price, notional input tax, valuation, supplier, VIN or
# an internal note (ADR-029); tests/test_kan225_public_stock_projection.py
# holds the list and the refusal by name.
PUBLIC_STOCK_FIELDS = frozenset(
    {
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
    }
)


def allowed_listing(listing: dict[str, Any] | None) -> dict[str, Any] | None:
    if listing is None:
        return None
    return {key: value for key, value in listing.items() if key in PUBLIC_STOCK_FIELDS}


def _parse_time(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def apply_listing_changed(db: Session, *, tenant_id: uuid.UUID, payload: dict[str, Any]) -> bool:
    """Applies one statement; returns whether it changed the projection.
    Never commits (the consumer harness does, with its processed-event row)."""

    stock_item_id = uuid.UUID(payload["stockItemId"])
    revision = int(payload["revision"])
    row = db.scalar(
        select(PublicStockListing).where(PublicStockListing.stock_item_id == stock_item_id).with_for_update()
    )
    if row is not None and (row.tenant_id != tenant_id or row.revision >= revision):
        return False
    if row is None:
        row = PublicStockListing(tenant_id=tenant_id, stock_item_id=stock_item_id, revision=revision)
        db.add(row)
    listed = bool(payload["listed"])
    row.revision = revision
    row.is_listed = listed
    row.location_id = uuid.UUID(payload["locationId"]) if payload.get("locationId") else None
    row.listing = allowed_listing(payload.get("listing")) if listed else None
    row.published_at = _parse_time(payload.get("listedAt")) if listed else None
    row.updated_at = utcnow()
    db.flush()
    return True


@dataclass(frozen=True)
class PublicStockItem:
    stock_item_id: uuid.UUID
    location_id: uuid.UUID | None
    published_at: dt.datetime | None
    listing: dict[str, Any]


def list_public_stock(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None = None
) -> list[PublicStockItem]:
    stmt = select(PublicStockListing).where(
        PublicStockListing.tenant_id == dealership_id, PublicStockListing.is_listed.is_(True)
    )
    if location_id is not None:
        stmt = stmt.where(PublicStockListing.location_id == location_id)
    rows = db.scalars(stmt.order_by(PublicStockListing.published_at.desc(), PublicStockListing.id)).all()
    return [
        PublicStockItem(
            stock_item_id=row.stock_item_id,
            location_id=row.location_id,
            published_at=row.published_at,
            listing=allowed_listing(row.listing) or {},
        )
        for row in rows
    ]


def count_public_stock(db: Session, *, dealership_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(PublicStockListing)
            .where(PublicStockListing.tenant_id == dealership_id, PublicStockListing.is_listed.is_(True))
        )
        or 0
    )
