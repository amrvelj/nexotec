"""Vehicle endpoints (issue #5): global profile + custody chain.

Visibility rule (Swiss addendum Round 3): static profile (VIN/specs/energy
label) is visible to any authenticated dealer, cross-tenant — required for
duplicate-VIN prevention and trade-in workflows. `status` and
`current_custodian_partner_id` are redacted (null) unless the requester is
the current custodian or platform_admin, so a dealer can't see which
competitor currently holds a VIN just by looking it up.
`GET .../custody-events` is row-filtered to the requester's own tenant's
events; platform_admin sees the full chain.

No dedicated audit-log endpoint here (unlike Dealer/Customer) — the
original spec's Vehicle endpoint list never included one, and exposing the
full audit trail (which includes other dealers' custody transitions) to any
dealer_admin would leak exactly the competitive data the visibility rule
above protects against. Audit events are still recorded unconditionally
(record_audit_event calls in services/vehicle.py), just not read back via
an API yet — flagged as a deliberate scoping choice, not an oversight.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ForbiddenError
from app.core.pagination import PageParams, page_params
from app.db import get_db
from app.models.vehicle import CustodyEventType, VehicleStatus
from app.schemas.vehicle import (
    CustodyEventCreate,
    CustodyEventPage,
    CustodyEventRead,
    VehicleCreate,
    VehiclePage,
    VehicleRead,
    VehicleUpdate,
)
from app.services import vehicle as vehicle_service
from app.services.idempotency import find_cached_response, store_response

router = APIRouter(tags=["vehicles"])

_WRITE_ROLES = (AccessRole.DEALER_ADMIN, AccessRole.INVENTORY)


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


def _serialize_vehicle(vehicle, principal: Principal) -> VehicleRead:
    is_custodian_or_admin = principal.access_role == AccessRole.PLATFORM_ADMIN or (
        vehicle.current_custodian_partner_id is not None
        and principal.tenant_id == vehicle.current_custodian_partner_id
    )
    data = VehicleRead.model_validate(vehicle, from_attributes=True)
    if not is_custodian_or_admin:
        data = data.model_copy(update={"status": None, "current_custodian_partner_id": None})
    return data


@router.post("/vehicles", response_model=VehicleRead, status_code=201)
def create_vehicle(
    body: VehicleCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    vehicle = vehicle_service.create_vehicle(
        db, data=body, custodian_partner_id=principal.tenant_id, actor_id=principal.user_id
    )
    result = _serialize_vehicle(vehicle, principal)

    if idempotency_key:
        store_response(
            db,
            tenant_id=principal.tenant_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.get("/vehicles/by-vin/{vin}", response_model=VehicleRead)
def get_vehicle_by_vin(
    vin: str, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    vehicle = vehicle_service.get_vehicle_by_vin_or_404(db, vin)
    return _serialize_vehicle(vehicle, principal)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(
    vehicle_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    return _serialize_vehicle(vehicle, principal)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    check_version(vehicle.version, if_match, entity_name="Vehicle")

    if "status" in body.model_fields_set and body.status is not None:
        is_custodian = (
            vehicle.current_custodian_partner_id is not None
            and principal.tenant_id == vehicle.current_custodian_partner_id
        )
        if principal.access_role != AccessRole.PLATFORM_ADMIN and not is_custodian:
            raise ForbiddenError("Only the current custodian or platform_admin may change Vehicle status.")

    vehicle = vehicle_service.update_vehicle(db, vehicle=vehicle, data=body, actor_id=principal.user_id)
    return _serialize_vehicle(vehicle, principal)


@router.post("/vehicles/{vehicle_id}/custody-events", response_model=CustodyEventRead, status_code=201)
def create_custody_event(
    vehicle_id: uuid.UUID,
    body: CustodyEventCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role(*_WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    partner_id = body.partner_id or principal.tenant_id
    if principal.access_role != AccessRole.PLATFORM_ADMIN and partner_id != principal.tenant_id:
        raise ForbiddenError("Cannot record a custody event on another dealer's behalf.")

    if body.event_type == CustodyEventType.SOLD:
        is_custodian = (
            vehicle.current_custodian_partner_id is not None
            and partner_id == vehicle.current_custodian_partner_id
        )
        if principal.access_role != AccessRole.PLATFORM_ADMIN and not is_custodian:
            raise ForbiddenError("Only the current custodian may record a sale out of custody.")

    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    event = vehicle_service.create_custody_event(
        db,
        vehicle=vehicle,
        event_type=body.event_type,
        partner_id=partner_id,
        event_date=body.event_date,
        transaction_id=body.transaction_id,
        actor_id=principal.user_id,
    )
    result = CustodyEventRead.model_validate(event, from_attributes=True)

    if idempotency_key:
        store_response(
            db,
            tenant_id=principal.tenant_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.get("/vehicles/{vehicle_id}/custody-events", response_model=CustodyEventPage)
def list_custody_events(
    vehicle_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    vehicle_service.get_vehicle_or_404(db, vehicle_id)
    tenant_filter = None if principal.access_role == AccessRole.PLATFORM_ADMIN else principal.tenant_id
    rows, next_cursor = vehicle_service.list_custody_events(
        db, vehicle_id=vehicle_id, tenant_filter=tenant_filter, params=params
    )
    return CustodyEventPage(
        items=[CustodyEventRead.model_validate(e, from_attributes=True) for e in rows], next_cursor=next_cursor
    )


@router.get("/vehicles", response_model=VehiclePage)
def list_vehicles(
    status: VehicleStatus | None = None,
    custodian_partner_id: uuid.UUID | None = None,
    updated_since: dt.datetime | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows, next_cursor = vehicle_service.list_vehicles(
        db, status=status, custodian_partner_id=custodian_partner_id, updated_since=updated_since, params=params
    )
    return VehiclePage(items=[_serialize_vehicle(v, principal) for v in rows], next_cursor=next_cursor)
