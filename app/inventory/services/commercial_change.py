"""`inventory.stock_item.commercial_changed` (KAN-553, ADR-115).

An open offer or a pending contract freezes its car's commercial data at
generation (ADR-041) and keeps it; Sales is told when the car in stock
changed under it, so the seller can refresh or ignore it. This module is the
one writer of that fact: every write path that can change one of the fields
below takes `commercial_state` before its change and calls
`publish_if_changed` after it, in the same transaction (rule 4).

Fields: `listPrice`, `effectivePrice`, the factory-option set (`options`),
`exteriorColour`, `odometerKm`, `configurationLabel`. Not emitted by a
purchase booking or a storno — those have their own facts.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_option import StockItemOption

EVENT_TYPE = "inventory.stock_item.commercial_changed"
_PRICE_FIELDS = ("listPrice", "effectivePrice")


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(Decimal(value).quantize(Decimal("0.01")))


def commercial_state(db: Session, item: StockItem) -> dict[str, Any]:
    """The fields an open offer cares about, as they stand (flushed)."""

    options = db.execute(
        select(StockItemOption.code, StockItemOption.label, StockItemOption.price).where(
            StockItemOption.tenant_id == item.tenant_id, StockItemOption.stock_item_id == item.id
        )
    ).all()
    return {
        "listPrice": _money(item.list_price),
        "effectivePrice": _money(item.effective_price),
        "options": sorted((code, label, _money(price)) for code, label, price in options),
        "exteriorColour": item.exterior_colour,
        "odometerKm": item.odometer_km,
        "configurationLabel": item.configuration_label,
    }


def publish_if_changed(
    db: Session, *, item: StockItem, before: dict[str, Any], actor_id: uuid.UUID | None
) -> list[str]:
    """Publishes the fact when anything in `before` moved; returns the names
    of the fields that changed (none: nothing published). Flushes first, so
    the option rows a caller added are read."""

    db.flush()
    after = commercial_state(db, item)
    changed = [field for field in after if after[field] != before.get(field)]
    if not changed:
        return []
    publish(
        db,
        OutboxEvent(
            event_type=EVENT_TYPE,
            tenant_id=item.tenant_id,
            producer="inventory",
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={
                "stockNumber": item.stock_number,
                "vehicleLabel": item.vehicle_label,
                "changedFields": changed,
                "prices": {
                    field: {"old": before.get(field), "new": after[field]} for field in _PRICE_FIELDS if field in changed
                },
                "changedAt": utcnow().isoformat(),
                "changedBy": str(actor_id) if actor_id is not None else None,
            },
        ),
    )
    return changed
