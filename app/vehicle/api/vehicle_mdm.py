"""VehicleMdm endpoints (WP-5 PR-9): identity editing and the one search box.
«Allocate to customer» is customer's write and lives at the composition
root (`app/api/v1/vehicle_parties.py`, KAN-634). No tenant scoping on reads —
VehicleMdm is a global fact; writes require the "vehicle_mdm" capability.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams
from app.core.permissions import require_write
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.db import get_db
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.vehicle_mdm import (
    VehicleMdmCreate,
    VehicleMdmCreateResult,
    VehicleMdmDetailRead,
    VehicleMdmPage,
    VehicleMdmRead,
    VehicleMdmUpdate,
    VehicleSearchHit,
    VehicleSearchResult,
    VehicleStockItemLink,
)
from app.vehicle.services import plate as plate_service
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.plate_read_guard import read_plate_history
from app.vehicle.services.search import filter_vehicles, resolve_identifier
from app.vehicle.services.stock_holding import held_stock_item

router = APIRouter(tags=["vehicle-mdm"], route_class=IdempotentRoute)
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
    picks. When `q` resolves as an identifier, `resolved` (or
    `pickerCandidates`) carries the answer and `filtered` is an empty page
    (KAN-82): never a page unrelated to `q` beside the hit. Otherwise —
    free text, or an identifier-shaped string that found neither a hit nor
    a picker — `filtered` is the ordinary grid filtered by `q` and the
    other two are empty. The fall-through matters because the shapes are
    loose: a half-typed vehicle number (`F-0001`) or VIN prefix (`VF1`)
    looks like a plate, and must still filter; a real identifier that
    matches nothing filters to an empty page, i.e. "no such vehicle".
    `sort`, the cursor and the count apply to `filtered` only.

    The Vehicles screen keeps its grid where it was under a hit (FR-V-06,
    "resolves above the grid") by reading the unfiltered page itself.
    """

    sort_fields = parse_sort(sort, allowed=VEHICLE_MDM_SORT_FIELDS) or _DEFAULT_VEHICLE_MDM_SORT
    params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)

    resolution = resolve_identifier(db, q, group_id=principal.group_id) if q else None
    if resolution is not None and (resolution.resolved or resolution.picker_candidates):
        return VehicleSearchResult(
            resolved=_search_hit(db, resolution.resolved, principal) if resolution.resolved else None,
            picker_candidates=resolution.picker_candidates,
            filtered=_page([], None, 0, False),
        )

    rows, next_cursor, total, total_is_estimate = filter_vehicles(db, query=q or None, params=params)
    return VehicleSearchResult(
        resolved=None,
        picker_candidates=[],
        filtered=_page(rows, next_cursor, total, total_is_estimate),
    )


def _search_hit(db: Session, vehicle: VehicleMdm, principal: Principal) -> VehicleSearchHit:
    """The resolved vehicle plus its Kontrollschild valid today (KAN-82) —
    read through this one car's own plate history, the targeted read the
    non-enumerability rule allows (ADR-039), never a plate listing. That
    read is audited and limited like the Plates tab (KAN-231); past the
    limit the hit still resolves, with the plate withheld.
    """

    read = VehicleMdmRead.model_validate(vehicle, from_attributes=True)
    plates = read_plate_history(
        db,
        actor_id=principal.user_id,
        tenant_id=principal.tenant_id,
        group_id=principal.group_id,
        vehicle_id=vehicle.id,
        purpose="search_hit",
    )
    if plates is None:
        return VehicleSearchHit(**read.model_dump(), current_plate=None, current_plate_withheld=True)
    today = plate_service.swiss_today()  # KAN-467: the Swiss day, never the UTC one
    current_plate = next(
        (row.plate for row in plates if row.valid_from <= today and (row.valid_to is None or row.valid_to >= today)),
        None,
    )
    return VehicleSearchHit(**read.model_dump(), current_plate=current_plate)


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


def _detail(db: Session, vehicle: VehicleMdm, principal: Principal) -> VehicleMdmDetailRead:
    """KAN-667: the record, and the stock car of the caller's own Betrieb
    (the token's tenant, never a parameter) that holds it — another
    Betrieb's stock is never named (rule 7)."""

    held = held_stock_item(db, tenant_id=principal.tenant_id, vehicle_id=vehicle.id)
    read = VehicleMdmRead.model_validate(vehicle, from_attributes=True)
    return VehicleMdmDetailRead(
        **read.model_dump(),
        stock_item=(
            VehicleStockItemLink(id=held.stock_item_id, stock_number=held.stock_number) if held is not None else None
        ),
    )


@router.get("/vehicle-mdm/{vehicle_id}", response_model=VehicleMdmDetailRead)
def get_vehicle(
    vehicle_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    vehicle = vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    return _detail(db, vehicle, principal)


@router.patch("/vehicle-mdm/{vehicle_id}", response_model=VehicleMdmDetailRead)
def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehicleMdmUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """The identity fields (ADR-045). A VIN change on a car the caller's
    Betrieb holds in stock is 409 `vin_change_needs_vin_correction` (KAN-667):
    it is corrected there with «VIN korrigieren»."""

    vehicle = vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    check_version(vehicle.version, if_match, entity_name="VehicleMdm")
    vehicle = vehicle_mdm_service.update_vehicle_mdm(
        db, vehicle=vehicle, data=body, actor_id=principal.user_id, tenant_id=principal.tenant_id
    )
    return _detail(db, vehicle, principal)
