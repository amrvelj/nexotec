"""Reservation endpoints (WP-7 PR-4, ADR-047). Both require an
Idempotency-Key — a caller retrying after a timeout must be able to
safely resend."""

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.errors import BadRequestError
from app.core.permissions import require_write
from app.db import get_db
from app.inventory.schemas.reservation import ReservationRead, ReserveRequest
from app.inventory.services import reservation as reservation_service

router = APIRouter(tags=["inventory"])


def _required_idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    if not idempotency_key:
        raise BadRequestError("Idempotency-Key header is required for this call.")
    return idempotency_key


@router.post("/inventory/stock-items/{stock_item_id}/reservations", response_model=ReservationRead, status_code=201)
def create_reservation(
    stock_item_id: uuid.UUID,
    body: ReserveRequest,
    idempotency_key: str = Depends(_required_idempotency_key),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    result = reservation_service.reserve(
        db,
        tenant_id=principal.tenant_id,
        stock_item_id=stock_item_id,
        contract_id=body.contract_id,
        idempotency_key=idempotency_key,
    )
    return ReservationRead(reservation_id=result["reservationId"], stock_item_id=result["stockItemId"])


@router.post("/inventory/reservations/{reservation_id}/release", status_code=200)
def release_reservation(
    reservation_id: uuid.UUID,
    idempotency_key: str = Depends(_required_idempotency_key),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    return reservation_service.release(
        db, tenant_id=principal.tenant_id, reservation_id=reservation_id, idempotency_key=idempotency_key
    )
