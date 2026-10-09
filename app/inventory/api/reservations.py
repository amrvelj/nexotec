"""Reservation endpoints (WP-7 PR-4, ADR-047). Both honour an optional
Idempotency-Key through IdempotentRoute (KAN-266): a caller retrying after a
timeout resends under the same key and gets the first answer back. The key
used to be required and stored by the reservation service itself; the route
holds it now, so these call the service's *_without_key entries."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_write
from app.db import get_db
from app.inventory.schemas.reservation import ReservationRead, ReserveRequest
from app.inventory.services import reservation as reservation_service

router = APIRouter(tags=["inventory"], route_class=IdempotentRoute)


@router.post("/inventory/stock-items/{stock_item_id}/reservations", response_model=ReservationRead, status_code=201)
def create_reservation(
    stock_item_id: uuid.UUID,
    body: ReserveRequest,
    principal: Principal = Depends(require_write("stock_items")),
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
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    return reservation_service.release_without_key(db, tenant_id=principal.tenant_id, reservation_id=reservation_id)
