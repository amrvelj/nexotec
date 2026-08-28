"""VehicleMdm endpoints (WP-5 PR-9): identity editing, the one search box,
and allocating a vehicle to a customer. No tenant scoping on reads —
VehicleMdm is a global fact, same as the shipped table it replaces
(app.vehicle.api.vehicles's own docstring); writes require the
"vehicle_mdm" capability, same gate the old endpoints already used.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.pagination import PageParams, page_params
from app.core.permissions import require_write
from app.customer.public import allocate_vehicle_party
from app.db import get_db
from app.vehicle.schemas.vehicle_mdm import (
    VehicleAllocatePartyRequest,
    VehicleMdmCreate,
    VehicleMdmCreateResult,
    VehicleMdmPage,
    VehicleMdmRead,
    VehicleMdmUpdate,
    VehiclePartyAllocationRead,
    VehicleSearchResult,
)
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.search import filter_vehicles, resolve_identifier

router = APIRouter(tags=["vehicle-mdm"])


@router.get("/vehicle-mdm/search", response_model=VehicleSearchResult)
def search_vehicles(
    q: str = "",
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """FR-V-06/FR-V-16: ONE search box, two behaviours, decided by the
    string's own shape — never a second field, never a mode the caller
    picks. `resolved`/`pickerCandidates` are populated only when `q` looks
    like an identifier; otherwise `filtered` is the ordinary grid page and
    the other two are empty, exactly as if the user had typed a brand
    fragment.
    """

    resolution = resolve_identifier(db, q) if q else None
    if resolution is not None:
        rows, next_cursor = filter_vehicles(db, query=None, params=params)
        return VehicleSearchResult(
            resolved=VehicleMdmRead.model_validate(resolution.resolved, from_attributes=True)
            if resolution.resolved
            else None,
            picker_candidates=resolution.picker_candidates,
            filtered=VehicleMdmPage(
                items=[VehicleMdmRead.model_validate(v, from_attributes=True) for v in rows], next_cursor=next_cursor
            ),
        )

    rows, next_cursor = filter_vehicles(db, query=q or None, params=params)
    return VehicleSearchResult(
        resolved=None,
        picker_candidates=[],
        filtered=VehicleMdmPage(
            items=[VehicleMdmRead.model_validate(v, from_attributes=True) for v in rows], next_cursor=next_cursor
        ),
    )


@router.post("/vehicle-mdm", response_model=VehicleMdmCreateResult, status_code=200)
def create_vehicle(
    body: VehicleMdmCreate,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """Always 200, never 422/409 on a duplicate VIN (FR-V-15) — `created`
    tells the caller which case they're in; `vehicle` is a real, complete
    record either way, so the UI can offer to open it without a second call.
    """

    vehicle, created = vehicle_mdm_service.create_or_get_vehicle_mdm(
        db, vin=body.vin, catalogue_variant_id=body.catalogue_variant_id, stammnummer=body.stammnummer,
        type_approval_number=body.type_approval_number, first_registration_date=body.first_registration_date,
        actor_id=principal.user_id,
    )
    return VehicleMdmCreateResult(created=created, vehicle=VehicleMdmRead.model_validate(vehicle, from_attributes=True))


@router.get("/vehicle-mdm/{vehicle_id}", response_model=VehicleMdmRead)
def get_vehicle(
    vehicle_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    vehicle = vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    return VehicleMdmRead.model_validate(vehicle, from_attributes=True)


@router.patch("/vehicle-mdm/{vehicle_id}", response_model=VehicleMdmRead)
def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleMdmUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    vehicle = vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    check_version(vehicle.version, if_match, entity_name="VehicleMdm")
    vehicle = vehicle_mdm_service.update_vehicle_mdm(db, vehicle=vehicle, data=body, actor_id=principal.user_id)
    return VehicleMdmRead.model_validate(vehicle, from_attributes=True)


@router.post("/vehicle-mdm/{vehicle_id}/allocate", response_model=VehiclePartyAllocationRead, status_code=201)
def allocate_to_customer(
    vehicle_id: uuid.UUID,
    body: VehicleAllocatePartyRequest,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """FR-V-05's vehicle-side entry point — "Allocate to customer", the
    Vehicle 360 detail screen's alternative action (ADR-061). Calls
    exactly the same app.customer.public.allocate_vehicle_party the
    customer-side dialog uses, so the close-then-open semantics (ADR-064)
    are identical regardless of which record the user started from.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)  # 404s before touching customer at all
    party = allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=body.customer_id, role=body.role,
        group_id=principal.group_id, actor_id=principal.user_id,
    )
    return VehiclePartyAllocationRead.model_validate(party, from_attributes=True)
