"""The service catalogue in Dealer Administration (KAN-818, S1; build slice 1,
section 3.10).

Every route sits behind the router-level `aftersales` gate (D-A-04: 404 for
everyone without the module, before any role check), is the Betrieb's own
(tenant match; the platform tenant 404) and depends on the matrix row
`service_packages`: the role aftersales reads (the desk picks a package onto a
job), the manager writes (the ticket's criterion 6). The session guard counts
this router as Dealer Administration, so a manager sets the catalogue up
before activation (A-RULE-0; the data is Aftersales').

The package is the aggregate: a change is a PUT of the whole package with
If-Match on its version. A package is never deleted — `isActive: false`
takes it out of the offer (check 45). Every POST honours an Idempotency-Key
(the route class). A route decides no rule: it calls the service.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.aftersales.models.service_package import ServicePackage, ServicePackagePosition
from app.aftersales.schemas.service_package import (
    PackagePositionRead,
    PackageTimeRead,
    ServicePackageList,
    ServicePackageRead,
    ServicePackageWrite,
)
from app.aftersales.services import service_packages as service
from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.errors import NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.public import is_platform_dealership

router = APIRouter(
    tags=["service-catalogue"],
    route_class=IdempotentRoute,
    dependencies=[Depends(require_entitlement(ModuleCode.AFTERSALES))],
)

_TWO_PLACES = Decimal("0.01")
_BASE = "/dealerships/{dealership_id}/aftersales/service-packages"
_READ = require_read("service_packages")
_WRITE = require_write("service_packages")


def _guard(db: Session, dealership_id: uuid.UUID, principal: Principal) -> None:
    """The Betrieb's own and a dealer Betrieb — else 404."""

    require_tenant_match(dealership_id, principal)
    if is_platform_dealership(db, dealership_id):
        raise NotFoundError("Not found.")


def _position_read(position: ServicePackagePosition) -> PackagePositionRead:
    time = None
    if position.time_quantity is not None and position.time_unit is not None:
        # As stored (two decimals), also before the row is read back.
        per_hour = position.work_units_per_hour
        time = PackageTimeRead(
            quantity=position.time_quantity.quantize(_TWO_PLACES),
            unit=position.time_unit,
            work_units_per_hour=per_hour.quantize(_TWO_PLACES) if per_hour is not None else None,
        )
    return PackagePositionRead(
        id=position.id,
        sort_index=position.sort_index,
        kind=position.kind,
        text=position.text,
        labour_category_id=position.labour_category_id,
        labour_category_label=position.labour_category_label,
        time=time,
        check_needed=position.check_needed,
    )


def _read(package: ServicePackage, positions: list[ServicePackagePosition]) -> ServicePackageRead:
    return ServicePackageRead(
        id=package.id,
        name=package.name,
        name_translations=dict(package.name_translations or {}),
        is_active=package.is_active,
        positions=[_position_read(position) for position in positions],
        version=package.version,
        updated_at=package.updated_at,
    )


def _input(body: ServicePackageWrite) -> service.PackageInput:
    return service.PackageInput(
        name=body.name,
        name_translations=body.name_translations,
        is_active=body.is_active,
        positions=[
            service.PackagePositionInput(
                id=item.id,
                kind=item.kind,
                text=item.text,
                labour_category_id=item.labour_category_id,
                time=item.time,
                time_unit=item.time_unit,
                check_needed=item.check_needed,
            )
            for item in body.positions
        ],
    )


@router.get(_BASE, response_model=ServicePackageList)
def list_service_packages(
    dealership_id: uuid.UUID,
    active: bool = Query(default=False, description="Only the packages offered on a job (check 45)."),
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    packages = service.list_packages(db, dealership_id=dealership_id, active_only=active)
    positions = service.positions_of(db, [package.id for package in packages])
    return ServicePackageList(items=[_read(package, positions[package.id]) for package in packages])


@router.post(_BASE, response_model=ServicePackageRead, status_code=201)
def create_service_package(
    dealership_id: uuid.UUID,
    body: ServicePackageWrite,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    package, positions = service.create_package(
        db, dealership_id=dealership_id, actor_id=principal.user_id, data=_input(body)
    )
    return _read(package, positions)


@router.get(_BASE + "/{package_id}", response_model=ServicePackageRead)
def get_service_package(
    dealership_id: uuid.UUID,
    package_id: uuid.UUID,
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    package = service.get_package_or_404(db, dealership_id=dealership_id, package_id=package_id)
    return _read(package, service.positions_of(db, [package.id])[package.id])


@router.put(_BASE + "/{package_id}", response_model=ServicePackageRead)
def change_service_package(
    dealership_id: uuid.UUID,
    package_id: uuid.UUID,
    body: ServicePackageWrite,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    _guard(db, dealership_id, principal)
    package, positions = service.change_package(
        db,
        dealership_id=dealership_id,
        package_id=package_id,
        expected_version=if_match,
        actor_id=principal.user_id,
        data=_input(body),
    )
    return _read(package, positions)
