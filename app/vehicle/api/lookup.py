"""FR-V-14 shared identity lookup endpoint (WP-5 PR-6, ADR-043). No
tenant/group scoping at all — any authenticated principal may resolve a
vehicle already identified in vehicle-mdm. The response model
(SharedVehicleIdentity) is the actual enforcement of the licence boundary
— see tests/architecture/test_shared_identity_lookup_response_shape.py.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.errors import NotFoundError
from app.db import get_db
from app.vehicle.schemas.lookup import SharedVehicleIdentity
from app.vehicle.services.lookup import resolve_shared_identity

router = APIRouter(tags=["vehicle-mdm"])


@router.get("/vehicle-mdm/lookup", response_model=SharedVehicleIdentity)
def lookup_shared_identity(
    vin: str | None = None,
    stammnummer: str | None = None,
    vehicle_number: str | None = None,
    plate: str | None = None,
    canton: str | None = None,
    principal: Principal = Depends(get_current_principal),  # authenticated only — deliberately no tenant scope
    db: Session = Depends(get_db),
):
    result = resolve_shared_identity(
        db, vin=vin, stammnummer=stammnummer, vehicle_number=vehicle_number, plate=plate, canton=canton
    )
    if result is None:
        raise NotFoundError("No vehicle was found for the given identifier.")
    return result
