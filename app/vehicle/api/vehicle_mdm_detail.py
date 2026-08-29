"""Vehicle 360 detail-screen endpoints (WP-5 PR-9, FR-V-16): plates,
odometer, accessories and party roles, each scoped by a known vehicle id
— never a bare browse. Same "vehicle_mdm" write capability as the rest of
this context's endpoints.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.errors import NotFoundError
from app.core.permissions import require_write
from app.customer.public import list_vehicle_parties
from app.db import get_db
from app.vehicle.models.vehicle_history import VehicleAccessory
from app.vehicle.schemas.vehicle_mdm import (
    VehicleAccessoryCreate,
    VehicleAccessoryRead,
    VehicleOdometerReadingCreate,
    VehicleOdometerReadingRead,
    VehiclePartyAllocationRead,
    VehiclePlateRead,
)
from app.vehicle.services import vehicle_history as history_service
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.plate import list_plates_for_vehicle

router = APIRouter(tags=["vehicle-mdm-detail"])


@router.get("/vehicle-mdm/{vehicle_id}/plates", response_model=list[VehiclePlateRead])
def list_plates(
    vehicle_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    rows = list_plates_for_vehicle(db, vehicle_id=vehicle_id)
    return [VehiclePlateRead.model_validate(r, from_attributes=True) for r in rows]


@router.get("/vehicle-mdm/{vehicle_id}/odometer-readings", response_model=list[VehicleOdometerReadingRead])
def list_odometer_readings(
    vehicle_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    """Every reading, implausible ones included and clearly flagged — the
    amended FR-V-07 rule enforced at the response boundary: there is no
    query parameter here that could exclude one.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    rows = history_service.list_odometer_readings(db, vehicle_id=vehicle_id)
    return [VehicleOdometerReadingRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/vehicle-mdm/{vehicle_id}/odometer-readings", response_model=VehicleOdometerReadingRead, status_code=201
)
def record_odometer_reading(
    vehicle_id: uuid.UUID,
    body: VehicleOdometerReadingCreate,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    reading = history_service.record_odometer_reading(
        db, vehicle_id=vehicle_id, value=body.value, reading_date=body.reading_date, source=body.source,
        recording_tenant_id=principal.tenant_id,
    )
    return VehicleOdometerReadingRead.model_validate(reading, from_attributes=True)


@router.get("/vehicle-mdm/{vehicle_id}/accessories", response_model=list[VehicleAccessoryRead])
def list_accessories(
    vehicle_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    rows = list(
        db.scalars(
            select(VehicleAccessory)
            .where(VehicleAccessory.vehicle_id == vehicle_id)
            .order_by(VehicleAccessory.valid_from.desc())
        ).all()
    )
    return [VehicleAccessoryRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("/vehicle-mdm/{vehicle_id}/accessories", response_model=VehicleAccessoryRead, status_code=201)
def add_accessory(
    vehicle_id: uuid.UUID,
    body: VehicleAccessoryCreate,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    accessory = history_service.add_accessory(
        db, vehicle_id=vehicle_id, accessory_type=body.accessory_type, description=body.description,
        valid_from=body.valid_from, recording_tenant_id=principal.tenant_id,
    )
    return VehicleAccessoryRead.model_validate(accessory, from_attributes=True)


@router.delete("/vehicle-mdm/{vehicle_id}/accessories/{accessory_id}", status_code=204)
def remove_accessory(
    vehicle_id: uuid.UUID,
    accessory_id: uuid.UUID,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """Sets valid_to; the row is never deleted (FR-V-13)."""

    accessory = db.get(VehicleAccessory, accessory_id)
    if accessory is None or accessory.vehicle_id != vehicle_id:
        raise NotFoundError(f"Accessory {accessory_id} was not found.")
    history_service.remove_accessory(db, accessory=accessory, valid_to=dt.datetime.now(dt.UTC).date())


@router.get("/vehicle-mdm/{vehicle_id}/party-roles", response_model=list[VehiclePartyAllocationRead])
def list_party_roles(
    vehicle_id: uuid.UUID,
    include_closed: bool = False,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """FR-V-16: current owner/keeper/driver, plus former allocations on
    request (include_closed=True) — never mixed into the default view.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    rows = list_vehicle_parties(db, vehicle_id=vehicle_id, include_closed=include_closed)
    return [VehiclePartyAllocationRead.model_validate(r, from_attributes=True) for r in rows]
