"""A car in stock for a test that needs one (KAN-554).

Since KAN-554 (ADR-116) a create with a VIN lands in pipeline: only purchase
booking puts a car on stock. A test whose subject is not the purchase still
needs a car in stock, so it creates the item and promotes it (FR-V-04) — the
car is in stock under its VIN, with its vehicle-mdm record, and no purchase
booked, exactly what a create with a VIN gave before."""

import uuid

from sqlalchemy.orm import Session

from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.stock_item import create_stock_item


def create_in_stock_item(
    db: Session, *, tenant_id: uuid.UUID, data: StockItemCreate, actor_id: uuid.UUID | None = None
) -> StockItem:
    """Create the item with its VIN, then promote it into stock."""

    assert data.vin is not None, "a car in stock has a VIN"
    item = create_stock_item(db, tenant_id=tenant_id, data=data, actor_id=actor_id)
    return promote_to_vehicle_mdm(db, item=item, vin=item.vin)


def promote_created_item(db: Session, *, stock_item_id: uuid.UUID | str) -> StockItem:
    """For a test that created the item over HTTP with a VIN: promote it into
    stock (FR-V-04) under the VIN it carries."""

    item = db.get(StockItem, uuid.UUID(str(stock_item_id)))
    assert item is not None and item.vin is not None
    item = promote_to_vehicle_mdm(db, item=item, vin=item.vin)
    return item
