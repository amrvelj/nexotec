"""KAN-523 — which vehicle-mdm record a stock item is, for Sales' «Rechnung
anfordern»: the plate a sold car leaves with is recorded on that record
(the VIN's, Anto 2026-11-01). A plain dict, never an ORM row, as
`get_stock_item_pricing` (the caller must not hold an inventory object)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.models.stock_item import StockItem
from app.inventory.services.stock_item import get_stock_item_or_404


def get_stock_item_vehicle(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> dict:
    """`vehicleId` and `vin` of the dealership's stock item — both null for a
    pipeline item not yet promoted (ADR-045). 404 for another dealership's
    item, as for one that does not exist."""

    item = get_stock_item_or_404(db, tenant_id, stock_item_id)
    return {
        "vehicleId": item.vehicle_id,
        "vin": item.vin,
    }


@dataclass(frozen=True)
class HeldVehicleRecord:
    """KAN-667 — one stock item that holds a vehicle-mdm record: it has one
    (promotion gave it, ADR-045) and has not left stock. A stornoed car still
    holds it (it keeps its VIN and record, KAN-146). Plain data."""

    tenant_id: uuid.UUID
    stock_item_id: uuid.UUID
    stock_number: str
    vehicle_id: uuid.UUID


def list_held_vehicle_records(db: Session, *, after: uuid.UUID | None, limit: int) -> list[HeldVehicleRecord]:
    """Every Betrieb's stock items that hold a vehicle-mdm record, by id,
    a page of `limit` after `after` — for Vehicle's projection backfill
    (scripts/backfill_vehicle_stock_holding.py), the composition code that
    joins the two contexts. A SELECT only."""

    stmt = (
        select(StockItem.id, StockItem.tenant_id, StockItem.stock_number, StockItem.vehicle_id)
        .where(StockItem.vehicle_id.is_not(None), StockItem.left_stock_at.is_(None))
        .order_by(StockItem.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(StockItem.id > after)
    return [
        HeldVehicleRecord(
            tenant_id=row.tenant_id, stock_item_id=row.id, stock_number=row.stock_number, vehicle_id=row.vehicle_id
        )
        for row in db.execute(stmt).all()
    ]
