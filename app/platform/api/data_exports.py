"""Data export and offboarding (KAN-218; Dealer Administration D-A-31,
FR-A-44/45; Anto, 2026-11-13).

The Betrieb's own exports — `/v1/dealerships/{id}/exports` — are the
manager's (matrix row «Data export»: the manager requests, nobody edits).
Platform staff pass that row like every row, but the Betrieb's export is the
manager's own: staff neither request nor acknowledge nor download it there —
they produce an archive export with a reason on the platform path, and the
offboarding needs the MANAGER's acknowledgement (FR-A-45).

The platform paths — `/v1/platform/dealerships/{id}/…` — are staff's, from a
session with a second factor (D-A-36): the offboarding state, the
offboarding itself, and the archive exports for a former manager (Q2 B).

No signed URL yet (KAN-402): a download is this authenticated GET, private
and never cached, audited.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, require_access_role, require_mfa_session
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.core.idempotent_route import IdempotentRoute
from app.core.object_store import ObjectStore, get_object_store
from app.core.pagination import PageParams, page_params
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.export_job import ExportJob, ExportJobKind
from app.platform.schemas.data_export import (
    ArchiveExportRequest,
    ExportPage,
    ExportRead,
    OffboardingRead,
    OffboardRequest,
)
from app.platform.services import data_export as export_service
from app.platform.services import dealership as dealership_service
from app.platform.services import offboarding as offboarding_service

router = APIRouter(tags=["data-export"], route_class=IdempotentRoute)

_DOWNLOAD_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def _read(job: ExportJob) -> ExportRead:
    return ExportRead.model_validate(job, from_attributes=True)


def _page(rows: list[ExportJob], next_cursor: str | None) -> ExportPage:
    return ExportPage(items=[_read(row) for row in rows], next_cursor=next_cursor)


def _the_betriebs_own(principal: Principal) -> None:
    """The Betrieb's export is its manager's; staff use the archive export."""

    if AccessRole.PLATFORM_ADMIN in principal.roles:
        raise ForbiddenError(
            "The Betrieb's own export is its manager's — staff produce an archive export on the platform path.",
            details={"reason": "staff_use_archive_export"},
        )


def _zip(job: ExportJob, data: bytes) -> Response:
    filename = f"nexotec-export-{job.id}.zip"
    return Response(
        content=data,
        media_type=export_service.CONTENT_TYPE,
        headers={**_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- the Betrieb's own exports (the manager) ----------------------------------------


@router.post("/dealerships/{dealership_id}/exports", response_model=ExportRead, status_code=202)
def request_export(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_write("data_export")),
    db: Session = Depends(get_db),
):
    """FR-A-44: a full export of the Betrieb, produced by the worker; the
    response is the requested job (202)."""

    require_tenant_match(dealership_id, principal)
    _the_betriebs_own(principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    job = export_service.request_export(
        db, dealership=dealership, kind=ExportJobKind.SELF_SERVICE, actor_id=principal.user_id
    )
    return _read(job)


@router.get("/dealerships/{dealership_id}/exports", response_model=ExportPage)
def list_exports(
    dealership_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_read("data_export")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    rows, next_cursor = export_service.list_exports(
        db, dealership_id=dealership_id, params=params, kind=ExportJobKind.SELF_SERVICE
    )
    return _page(rows, next_cursor)


@router.get("/dealerships/{dealership_id}/exports/{export_id}", response_model=ExportRead)
def get_export(
    dealership_id: uuid.UUID,
    export_id: uuid.UUID,
    principal: Principal = Depends(require_read("data_export")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    return _read(
        export_service.get_export_or_404(
            db, dealership_id=dealership_id, export_id=export_id, kind=ExportJobKind.SELF_SERVICE
        )
    )


@router.get("/dealerships/{dealership_id}/exports/{export_id}/download")
def download_export(
    dealership_id: uuid.UUID,
    export_id: uuid.UUID,
    principal: Principal = Depends(require_read("data_export")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    require_tenant_match(dealership_id, principal)
    _the_betriebs_own(principal)
    job = export_service.get_export_or_404(
        db, dealership_id=dealership_id, export_id=export_id, kind=ExportJobKind.SELF_SERVICE
    )
    return _zip(job, export_service.download(db, job=job, actor_id=principal.user_id, store=store))


@router.post("/dealerships/{dealership_id}/exports/{export_id}/acknowledge", response_model=ExportRead)
def acknowledge_export(
    dealership_id: uuid.UUID,
    export_id: uuid.UUID,
    principal: Principal = Depends(require_write("data_export")),
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    """FR-A-45: the manager confirms the final export arrived."""

    require_tenant_match(dealership_id, principal)
    _the_betriebs_own(principal)
    job = export_service.get_export_or_404(
        db, dealership_id=dealership_id, export_id=export_id, kind=ExportJobKind.SELF_SERVICE, for_update=True
    )
    check_version(job.version, if_match, entity_name="Export")
    return _read(export_service.acknowledge(db, job=job, actor_id=principal.user_id))


# --- platform staff: offboarding and the archive exports -------------------------


def _offboarding_read(db: Session, dealership) -> OffboardingRead:
    state = offboarding_service.readiness(db, dealership)
    return OffboardingRead(
        dealership_id=dealership.id,
        legal_name=dealership.legal_name,
        status=dealership.status,
        version=dealership.version,
        offboarded_at=dealership.offboarded_at,
        retention_until=dealership.retention_until,
        erased_at=dealership.erased_at,
        last_write_at=state.last_write_at,
        acknowledged_export_id=state.export.id if state.export else None,
        acknowledged_export_produced_at=state.export.produced_at if state.export else None,
        refusal=state.refusal,
    )


@router.get(
    "/platform/dealerships/{dealership_id}/offboarding",
    response_model=OffboardingRead,
    dependencies=[Depends(require_mfa_session())],
)
def get_offboarding(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    return _offboarding_read(db, dealership_service.get_dealership_or_404(db, dealership_id))


@router.post(
    "/platform/dealerships/{dealership_id}/offboard",
    response_model=OffboardingRead,
    dependencies=[Depends(require_mfa_session())],
)
def offboard_dealership(
    dealership_id: uuid.UUID,
    body: OffboardRequest,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    """FR-A-45: refused (409) without an export the manager acknowledged
    and produced after the Betrieb's last write."""

    if body.confirmation is not True:
        raise UnprocessableEntityError(
            "Offboarding deactivates every user and cannot be undone — send confirmation: true.",
            details={"reason": "offboarding_confirmation_required"},
        )
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = offboarding_service.offboard(db, dealership=dealership, actor_id=principal.user_id, reason=body.reason)
    return _offboarding_read(db, dealership)


@router.get(
    "/platform/dealerships/{dealership_id}/exports",
    response_model=ExportPage,
    dependencies=[Depends(require_mfa_session())],
)
def list_platform_exports(
    dealership_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Every export of the Betrieb — its manager's and staff's archive
    exports — for the offboarding page."""

    dealership_service.get_dealership_or_404(db, dealership_id)
    rows, next_cursor = export_service.list_exports(db, dealership_id=dealership_id, params=params)
    return _page(rows, next_cursor)


@router.post(
    "/platform/dealerships/{dealership_id}/exports",
    response_model=ExportRead,
    status_code=202,
    dependencies=[Depends(require_mfa_session())],
)
def request_archive_export(
    dealership_id: uuid.UUID,
    body: ArchiveExportRequest,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    """Q2 B: an export from the archive for a former manager who asked
    support — audited with the requester and the reason."""

    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    job = export_service.request_export(
        db, dealership=dealership, kind=ExportJobKind.ARCHIVE, actor_id=principal.user_id, reason=body.reason
    )
    return _read(job)


@router.get(
    "/platform/dealerships/{dealership_id}/exports/{export_id}/download",
    dependencies=[Depends(require_mfa_session())],
)
def download_archive_export(
    dealership_id: uuid.UUID,
    export_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """Staff hand an archive export over; only an archive export — the
    manager's own stays the manager's."""

    job = export_service.get_export_or_404(
        db, dealership_id=dealership_id, export_id=export_id, kind=ExportJobKind.ARCHIVE
    )
    return _zip(job, export_service.download(db, job=job, actor_id=principal.user_id, store=store))
