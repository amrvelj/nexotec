"""KAN-270 — vehicle's daily purge of configurations no host ever took asks
Stock which of them a stock item names now: the safety net beside
`inventory.stock_item.added` / `inventory.stock_item.configuration_attached`,
which carry `configurationId` (`app/vehicle/services/configuration_retention.py`).
Every stock item counts, in every Betrieb, whatever its lifecycle — a
reference is never deleted under its host. A SELECT only; the caller batches
the ids."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.models.stock_item import StockItem


def referenced_configuration_ids(db: Session, configuration_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    ids = list(dict.fromkeys(configuration_ids))
    if not ids:
        return set()
    held = db.scalars(select(StockItem.configuration_id).where(StockItem.configuration_id.in_(ids)))
    return {value for value in held if value is not None}
