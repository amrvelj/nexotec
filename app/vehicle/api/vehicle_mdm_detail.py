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
from app.core.concurrency import require_if_match
from app.core.errors import ForbiddenError, NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_read, require_write
from app.customer.public import list_vehicle_party_holders
from app.db import get_db
from app.vehicle.models.vehicle_history import VehicleAccessory
from app.vehicle.schemas.vehicle_mdm import (
    CatalogueColourRead,
    CatalogueImageRead,
    CatalogueOptionRead,
    CatalogueOptionRelationRead,
    CatalogueSpecificationRead,
    CatalogueTyreSpecRead,
    VehicleAccessoryCreate,
    VehicleAccessoryRead,
    VehicleOdometerReadingCreate,
    VehicleOdometerReadingRead,
    VehiclePartyAllocationRead,
    VehiclePlateCreate,
    VehiclePlateEnd,
    VehiclePlateRead,
)
from app.vehicle.services import catalogue_entitlements
from app.vehicle.services import plate as plate_service
from app.vehicle.services import vehicle_history as history_service
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.plate_read_guard import REFUSAL_REASON, read_plate_history

router = APIRouter(tags=["vehicle-mdm-detail"], route_class=IdempotentRoute)


@router.get("/vehicle-mdm/{vehicle_id}/plates", response_model=list[VehiclePlateRead])
def list_plates(
    vehicle_id: uuid.UUID,
    principal: Principal = Depends(require_read("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """Audited and limited per user (KAN-231, `services/plate_read_guard.py`):
    past the limit this answers 403 with `details.reason`
    `plate_read_limit_reached`, and the refusal is audited too.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    rows = read_plate_history(
        db,
        actor_id=principal.user_id,
        tenant_id=principal.tenant_id,
        group_id=principal.group_id,
        vehicle_id=vehicle_id,
        purpose="plates_tab",
    )
    if rows is None:
        raise ForbiddenError(
            "Too many vehicles' plates read in a short time; try again later.",
            details={"reason": REFUSAL_REASON},
        )
    return [VehiclePlateRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("/vehicle-mdm/{vehicle_id}/plates", response_model=VehiclePlateRead, status_code=201)
def record_plate(
    vehicle_id: uuid.UUID,
    body: VehiclePlateCreate,
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """KAN-467 — «Kontrollschild erfassen», in the caller's group's register
    (ADR-039 as amended). 409 `plate_current_on_other_vehicle` names the
    group's car(s) the plate is on; the same POST with `moveFromPlateIds` or
    `wechselschildWithPlateId` is the user's confirmation. Audited without
    the plate value; an `Idempotency-Key` makes a retry answer the first
    recording."""

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    row = plate_service.record_plate(
        db,
        vehicle_id=vehicle_id,
        group_id=principal.group_id,
        recording_tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        plate=body.plate,
        canton=body.canton,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        move_from_plate_ids=body.move_from_plate_ids,
        wechselschild_with_plate_id=body.wechselschild_with_plate_id,
    )
    db.commit()
    db.refresh(row)
    return VehiclePlateRead.model_validate(row, from_attributes=True)


@router.post("/vehicle-mdm/{vehicle_id}/plates/{plate_id}/end", response_model=VehiclePlateRead)
def end_plate(
    vehicle_id: uuid.UUID,
    plate_id: uuid.UUID,
    body: VehiclePlateEnd,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """KAN-467 — «beenden»: sets `validTo`; the row is never deleted. 404 for
    an assignment of another group's register."""

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    row = plate_service.get_vehicle_plate_or_404(
        db, vehicle_id=vehicle_id, plate_id=plate_id, group_id=principal.group_id
    )
    plate_service.end_plate(
        db, row=row, valid_to=body.valid_to, expected_version=if_match, actor_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    db.commit()
    db.refresh(row)
    return VehiclePlateRead.model_validate(row, from_attributes=True)


@router.get("/vehicle-mdm/{vehicle_id}/catalogue-specification", response_model=CatalogueSpecificationRead)
def get_catalogue_specification(
    vehicle_id: uuid.UUID,
    principal: Principal = Depends(require_read("vehicle_mdm")),
    db: Session = Depends(get_db),
):
    """WP-6 PR-5 — degrades per capability against the CALLING tenant's
    own contract (ADR-013's own per-dealer mirror means two dealers can
    legitimately see two different degraded views of the same global
    vehicle). Reads PR-4's already-synced mirror only, never a live
    provider call — this is a fast, cacheable read, not a sync trigger.
    """

    vehicle_mdm = vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    result = catalogue_entitlements.get_catalogue_specification(
        db, tenant_id=principal.tenant_id, model_variant_id=vehicle_mdm.catalogue_variant_id
    )
    return CatalogueSpecificationRead(
        has_catalogue_match=result.has_catalogue_match,
        has_provider_connection=result.has_provider_connection,
        packages_available=result.packages_available,
        images_available=result.images_available,
        dealer_can_upload_images=result.dealer_can_upload_images,
        options=[CatalogueOptionRead.model_validate(o, from_attributes=True) for o in result.options],
        colours=[CatalogueColourRead.model_validate(c, from_attributes=True) for c in result.colours],
        tyre_specs=[CatalogueTyreSpecRead.model_validate(t, from_attributes=True) for t in result.tyre_specs],
        images=[CatalogueImageRead.model_validate(i, from_attributes=True) for i in result.images],
        option_relations=[
            CatalogueOptionRelationRead.model_validate(r, from_attributes=True) for r in result.option_relations
        ],
    )


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

    Scoped by the caller's own group_id (ADR-014/ADR-049): vehicle_mdm is
    a deliberately global fact, so a party row here may belong to a
    customer in a different dealer group entirely — see
    list_vehicle_parties' own docstring. The holder's name (KAN-140)
    comes from the same group-scoped read, never from a wider lookup.
    """

    vehicle_mdm_service.get_vehicle_mdm_or_404(db, vehicle_id)
    holders = list_vehicle_party_holders(
        db, vehicle_id=vehicle_id, group_id=principal.group_id, include_closed=include_closed
    )
    return [
        VehiclePartyAllocationRead.model_validate(party, from_attributes=True).model_copy(
            update={"display_name": display_name}
        )
        for party, display_name in holders
    ]
