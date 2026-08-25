"""Vehicle endpoints (issue #5): global profile + custody chain.

Visibility rule (Swiss addendum Round 3, refined by PM/CTO R1 ruling
2026-08-06): static profile (VIN/specs/energy label) is visible to any
authenticated dealer, cross-tenant — required for duplicate-VIN prevention
and trade-in workflows. `current_custodian_partner_id` is redacted (null)
unless the requester is the current custodian or platform_admin, so a
dealer can't see which competitor currently holds a VIN just by looking it
up. `status` has a broader visibility rule than custodian alone: current
custodian, OR platform_admin, OR the requester has ever appeared in this
vehicle's custody history (see has_custody_event_for_tenant) — this fixes
the original rule's defect where completing a sale cleared
current_custodian_partner_id and made the *selling* dealer blind to their
own vehicle's resulting "sold" status, not just competitors blind to it.
`GET .../custody-events` is row-filtered to the requester's own tenant's
events; platform_admin sees the full chain.

`GET .../audit-log` uses the same row-filtering rule (CTO ruling,
2026-08-06 — issue #7's acceptance criteria requires an audit-log endpoint
per entity, no exceptions for Vehicle): AuditEvent.tenant_id is set to
whichever dealer's action produced that row (the custodian for create/
custody-event actions, the editing dealer for spec-field updates — see
services/vehicle.py) instead of None, so the same partner_id-style filter
that already protects .../custody-events also protects the audit trail.
"""

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.audit import list_audit_events
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ConflictError, ForbiddenError
from app.core.idempotency import find_cached_response, store_response
from app.core.pagination import PageParams, page_params
from app.core.permissions import require_read, require_write
from app.db import get_db
from app.vehicle.models.vehicle import CustodyEventType, VehicleStatus
from app.vehicle.schemas.vehicle import (
    CustodyEventCreate,
    CustodyEventPage,
    CustodyEventRead,
    VehicleCreate,
    VehiclePage,
    VehicleRead,
    VehicleUpdate,
)
from app.vehicle.services import vehicle as vehicle_service

router = APIRouter(tags=["vehicles"])

# Endpoint-level guard, not services/vehicle.py's own _TERMINAL_STATUSES
# (which excludes SOLD — that set only blocks arbitrary status PATCH
# changes, a narrower concept). complete_transaction sets vehicle.status =
# SOLD *before* calling create_custody_event, so a service-level guard here
# would incorrectly reject the very call transitioning the vehicle into
# this state — the check belongs at the API boundary, on direct client
# requests only (CTO review, 2026-08-06).
_BLOCKED_CUSTODY_EVENT_STATUSES = (VehicleStatus.SOLD, VehicleStatus.TOTALED, VehicleStatus.SCRAPPED)


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


def _serialize_vehicle(vehicle, principal: Principal, db: Session) -> VehicleRead:
    is_custodian_or_admin = AccessRole.PLATFORM_ADMIN in principal.roles or (
        vehicle.current_custodian_partner_id is not None
        and principal.tenant_id == vehicle.current_custodian_partner_id
    )
    status_visible = is_custodian_or_admin or vehicle_service.has_custody_event_for_tenant(
        db, vehicle_id=vehicle.id, tenant_id=principal.tenant_id
    )

    data = VehicleRead.model_validate(vehicle, from_attributes=True)
    updates: dict[str, Any] = {}
    if not status_visible:
        updates["status"] = None
    if not is_custodian_or_admin:
        updates["current_custodian_partner_id"] = None
    if updates:
        data = data.model_copy(update=updates)
    return data


@router.post("/vehicles", response_model=VehicleRead, status_code=201)
def create_vehicle(
    body: VehicleCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_write("vehicle_mdm")),
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
    result = _serialize_vehicle(vehicle, principal, db)

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
    return _serialize_vehicle(vehicle, principal, db)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(
    vehicle_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    return _serialize_vehicle(vehicle, principal, db)


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    check_version(vehicle.version, if_match, entity_name="Vehicle")

    if "status" in body.model_fields_set and body.status is not None:
        is_custodian = (
            vehicle.current_custodian_partner_id is not None
            and principal.tenant_id == vehicle.current_custodian_partner_id
        )
        if AccessRole.PLATFORM_ADMIN not in principal.roles and not is_custodian:
            raise ForbiddenError("Only the current custodian or platform_admin may change Vehicle status.")

    vehicle = vehicle_service.update_vehicle(
        db, vehicle=vehicle, data=body, actor_id=principal.user_id, actor_tenant_id=principal.tenant_id
    )
    return _serialize_vehicle(vehicle, principal, db)


@router.post("/vehicles/{vehicle_id}/custody-events", response_model=CustodyEventRead, status_code=201)
def create_custody_event(
    vehicle_id: uuid.UUID,
    body: CustodyEventCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_service.get_vehicle_or_404(db, vehicle_id)
    if vehicle.status in _BLOCKED_CUSTODY_EVENT_STATUSES:
        raise ConflictError(
            f"Cannot record a custody event — vehicle status '{vehicle.status.value}' does not allow"
            " further custody changes.",
            details={"vehicleStatus": vehicle.status.value},
        )

    partner_id = body.partner_id or principal.tenant_id
    if AccessRole.PLATFORM_ADMIN not in principal.roles and partner_id != principal.tenant_id:
        raise ForbiddenError("Cannot record a custody event on another dealer's behalf.")

    if body.event_type == CustodyEventType.SOLD:
        is_custodian = (
            vehicle.current_custodian_partner_id is not None
            and partner_id == vehicle.current_custodian_partner_id
        )
        if AccessRole.PLATFORM_ADMIN not in principal.roles and not is_custodian:
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
    tenant_filter = None if AccessRole.PLATFORM_ADMIN in principal.roles else principal.tenant_id
    rows, next_cursor = vehicle_service.list_custody_events(
        db, vehicle_id=vehicle_id, tenant_filter=tenant_filter, params=params
    )
    return CustodyEventPage(
        items=[CustodyEventRead.model_validate(e, from_attributes=True) for e in rows], next_cursor=next_cursor
    )


@router.get("/vehicles/{vehicle_id}/audit-log", response_model=AuditEventPage)
def get_vehicle_audit_log(
    vehicle_id: uuid.UUID,
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    vehicle_service.get_vehicle_or_404(db, vehicle_id)
    tenant_filter = None if AccessRole.PLATFORM_ADMIN in principal.roles else principal.tenant_id
    events = list_audit_events(db, entity_type="vehicle", entity_id=vehicle_id, tenant_id=tenant_filter)
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in events], next_cursor=None
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
    return VehiclePage(items=[_serialize_vehicle(v, principal, db) for v in rows], next_cursor=next_cursor)
