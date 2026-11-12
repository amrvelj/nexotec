"""The customer's Vehicles tab (KAN-49, FR-19): `GET /v1/customers/{id}/vehicles`.

KAN-632 (ADR-131): customer sits below inventory in the layer order, so it
no longer asks Stock which of the customer's cars are in the group's stock.
The route lives at the composition root (`app/api/v1/customer_vehicles.py`),
which hands in Stock's answer as `stock_items_for`; this module builds the
page from customer's own rows and whatever it is handed. Path, response
shape and the KAN-378 entitlement rule are unchanged.
"""

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.customer.schemas.customer import CustomerVehiclePage, CustomerVehicleRead, VehicleStockLinkSummary
from app.customer.services.customer import (
    get_customer_or_404,
    list_customer_vehicles,
    list_other_vehicle_parties_batch,
)

StockItemsFor = Callable[[list[uuid.UUID]], Mapping[uuid.UUID, Mapping[str, Any]]]
"""Given the page's vehicle ids, the stock item (`id`, `stockNumber`) each one
currently has in the caller's group stock — only those that have one."""


def customer_vehicle_page(
    db: Session,
    *,
    group_id: uuid.UUID,
    customer_id: uuid.UUID,
    include_closed: bool,
    stock_items_for: StockItemsFor,
) -> CustomerVehiclePage:
    """The customer's party rows (404 outside the caller's group), each with
    the other open parties of its car and, when `stock_items_for` names one,
    the car's stock item. `stock_items_for` is asked once per page."""

    get_customer_or_404(db, group_id, customer_id)
    rows = list_customer_vehicles(db, customer_id=customer_id, include_closed=include_closed)
    vehicle_ids = [r.vehicle_id for r in rows]
    other_parties_by_vehicle = list_other_vehicle_parties_batch(
        db, vehicle_ids=vehicle_ids, exclude_customer_id=customer_id, group_id=group_id
    )
    stock_by_vehicle = stock_items_for(vehicle_ids) if vehicle_ids else {}
    items = [
        CustomerVehicleRead.model_validate(r, from_attributes=True).model_copy(
            update={
                "other_parties": other_parties_by_vehicle.get(r.vehicle_id, []),
                "stock_item": (
                    VehicleStockLinkSummary(**stock_by_vehicle[r.vehicle_id]) if r.vehicle_id in stock_by_vehicle else None
                ),
            }
        )
        for r in rows
    ]
    return CustomerVehiclePage(items=items)
