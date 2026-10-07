"""VehicleMdm endpoints (WP-5 PR-9): identity editing, the one search box,
and allocating a vehicle to a customer. No tenant scoping on reads —
VehicleMdm is a global fact, same as the shipped table it replaces
(app.vehicle.api.vehicles's own docstring); writes require the
"vehicle_mdm" capability, same gate the old endpoints already used.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import require_write
from app.core.sorting import SortField, parse_sort
from app.customer.public import allocate_vehicle_party, customer_display_name, get_customer_or_404
from app.db import get_db
from app.vehicle.models.vehicle_mdm import VehicleMdm
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
settings = get_settings()

# KAN-161 (U-02/U-03): every column of the vehicle list is sortable, and
# each one is indexed — vehicle_number/vin/stammnummer since rev
# 5c7e33d9cc78, the two statuses since rev a9d4c2e7f1b3. The statuses sort
# by their stored code, not by the translated label the grid shows.
VEHICLE_MDM_SORT_FIELDS: dict[str, object] = {
    "vehicleNumber": VehicleMdm.vehicle_number,
    "vin": VehicleMdm.vin,
    "stammnummer": VehicleMdm.stammnummer,
    "catalogueMatchStatus": VehicleMdm.catalogue_match_status,
    "vehicleStatus": VehicleMdm.vehicle_status,
}
# Declared per grid (FR-UI-01). Vehicle numbers are allocated in creation
# order, so this keeps the list's previous (created_at) order, on an index.
_DEFAULT_VEHICLE_MDM_SORT = [
    SortField(api_name="vehicleNumber", column=VehicleMdm.vehicle_number, direction="asc", nullable=False)
]


@router.get("/vehicle-mdm/search", response_model=VehicleSearchResult)
def search_vehicles(
    q: str = "",
    sort: str | None = Query(default=None, description="e.g. 'vin:asc,vehicleStatus:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """FR-V-06/FR-V-16: ONE search box, two behaviours, decided by the
    string's own shape — never a second field, never a mode the caller
    picks. `resolved`/`pickerCandidates` are populated only when `q` looks
    like an identifier; otherwise `filtered` is the ordinary grid page and
    the other two are empty, exactly as if the user had typed a brand
    fragment. `sort`, the cursor and the count apply to `filtered` only.
    """

    sort_fields = parse_sort(sort, allowed=VEHICLE_MDM_SORT_FIELDS) or _DEFAULT_VEHICLE_MDM_SORT
    params = SortPageParams(limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields)

    resolution = resolve_identifier(db, q) if q else None
    if resolution is not None:
        rows, next_cursor, total, total_is_estimate = filter_vehicles(db, query=None, params=params)
        return VehicleSearchResult(
            resolved=VehicleMdmRead.model_validate(resolution.resolved, from_attributes=True)
            if resolution.resolved
            else None,
            picker_candidates=resolution.picker_candidates,
            filtered=_page(rows, next_cursor, total, total_is_estimate),
        )

    rows, next_cursor, total, total_is_estimate = filter_vehicles(db, query=q or None, params=params)
    return VehicleSearchResult(
        resolved=None,
        picker_candidates=[],
        filtered=_page(rows, next_cursor, total, total_is_estimate),
    )


def _page(rows: list[VehicleMdm], next_cursor: str | None, total: int, total_is_estimate: bool) -> VehicleMdmPage:
    return VehicleMdmPage(
        items=[VehicleMdmRead.model_validate(v, from_attributes=True) for v in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
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

    Unlike the customer-side dialog, `body.customer_id` here names a
    customer the caller has not already reached through a group-scoped
    path — the customer-side route resolves `customer` via
    `get_customer_or_404(principal.group_id, ...)` before it ever calls
    `allocate_vehicle_party`, so it can never name another group's
    customer. This route must do the same lookup itself, or a caller
    could allocate a party against any customer_id it can guess,
    regardless of which group owns it. VehicleParty carries no group_id
    of its own to catch this at the write; a customer from another group
    is a 404, per rule #7, never a 403 and never a silent cross-group
    write.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)  # 404s before touching customer at all
    customer = get_customer_or_404(db, principal.group_id, body.customer_id)
    party = allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=body.customer_id, role=body.role,
        group_id=principal.group_id, actor_id=principal.user_id,
    )
    return VehiclePartyAllocationRead.model_validate(party, from_attributes=True).model_copy(
        update={"display_name": customer_display_name(customer)}
    )
