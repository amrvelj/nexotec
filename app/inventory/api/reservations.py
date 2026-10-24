"""Reservation endpoints (WP-7 PR-4, ADR-047). Both honour an optional
Idempotency-Key through IdempotentRoute (KAN-266): a caller retrying after a
timeout resends under the same key and gets the first answer back. The key
used to be required and stored by the reservation service itself; the route
holds it now, so these call the service's *_without_key entries.

Both are guarded by the matrix's "Stock reservation" row, whose write cell is
"Nobody" (KAN-376): a car is reserved and released by the sales contract
lifecycle in-process (ADR-047), so over HTTP only platform staff pass."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_write
from app.db import get_db
from app.inventory.schemas.reservation import ReservationRead, ReserveRequest
from app.inventory.services import reservation as reservation_service

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(tags=["inventory"], route_class=IdempotentRoute, dependencies=[Depends(require_entitlement(ModuleCode.SALES))])


@router.post("/inventory/stock-items/{stock_item_id}/reservations", response_model=ReservationRead, status_code=201)
def create_reservation(
    stock_item_id: uuid.UUID,
    body: ReserveRequest,
    principal: Principal = Depends(require_write("stock_reservation")),
    db: Session = Depends(get_db),
):
    result = reservation_service.reserve_without_key(
        db,
        tenant_id=principal.tenant_id,
        stock_item_id=stock_item_id,
        contract_id=body.contract_id,
    )
    return ReservationRead(reservation_id=result["reservationId"], stock_item_id=result["stockItemId"])


@router.post("/inventory/reservations/{reservation_id}/release", status_code=200)
def release_reservation(
    reservation_id: uuid.UUID,
    principal: Principal = Depends(require_write("stock_reservation")),
    db: Session = Depends(get_db),
):
    return reservation_service.release_without_key(db, tenant_id=principal.tenant_id, reservation_id=reservation_id)
