"""KAN-523 — which vehicle-mdm record a stock item is, for Sales' «Rechnung
anfordern»: the plate a sold car leaves with is recorded on that record
(the VIN's, Anto 2026-11-01). A plain dict, never an ORM row, as
`get_stock_item_pricing` (the caller must not hold an inventory object)."""

import uuid

from sqlalchemy.orm import Session

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
