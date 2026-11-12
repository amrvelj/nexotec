"""The customer's Vehicles tab read, composed here at the composition root
(KAN-632, ADR-131; the pattern KAN-629 set for the group-read flip).

Why this route lives in `app/api` and not in `app/customer/api`: the page is
customer's — its party rows, the other parties of each car — but it also
shows which of those cars are in the group's own stock, and that is Stock's
fact. Customer sits below inventory in the layer order, so it never imports
inventory; the route, which may import every context, asks Stock and hands
the answer into customer's page builder (`stock_items_for`).

Rule 11: one user request, two reads — customer's rows and Stock's one
batched lookup for the whole page — each answered by its owner, never a
third hop. Were the contexts extracted, this is a composition of two calls,
within the limit.

KAN-378 (Anto, option A): the stock summary is inventory data, licensed with
the sales module (D-A-04) — a Betrieb without it gets `stockItem: null`, as
it gets no /v1/inventory routes, and so does a token minted before the
module was taken away. Path, method, response and refusals (404 for a
customer outside the caller's group) are unchanged.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, holds_entitlement
from app.customer import public as customer
from app.db import get_db
from app.inventory.public import get_stock_items_for_vehicles

router = APIRouter(tags=["customers"], route_class=IdempotentRoute)


@router.get("/customers/{customer_id}/vehicles", response_model=customer.CustomerVehiclePage)
def list_customer_vehicles(
    customer_id: uuid.UUID,
    # KAN-49 / FR-19 — the Vehicles tab needs this customer's own historical
    # (ended) roles to remain readable, marked as ended (it already renders
    # `effectiveTo` for exactly this). Defaults to False, matching the
    # service's own default and every other caller of this route — only the
    # tab opts in, rather than the endpoint's default silently changing for
    # whoever else reads it.
    include_closed: bool = Query(default=False),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    def stock_items_for(vehicle_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        if not holds_entitlement(db, principal, ModuleCode.SALES):
            return {}
        return get_stock_items_for_vehicles(db, principal=principal, vehicle_ids=vehicle_ids)

    return customer.customer_vehicle_page(
        db,
        group_id=principal.group_id,
        customer_id=customer_id,
        include_closed=include_closed,
        stock_items_for=stock_items_for,
    )
