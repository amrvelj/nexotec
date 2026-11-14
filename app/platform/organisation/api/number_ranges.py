"""Number ranges — `/administration/number-ranges` (KAN-207; Dealer
Administration PRD "Number ranges", D-A-17, A-RULE-3).

Nested under /dealerships/{dealership_id}: tenant from the token
(`require_tenant_match`, 404-not-403 on another Betrieb's path id). Any role
reads (`require_read("number_ranges")`); the manager writes before a range
froze, a platform admin after, with a reason (the matrix row «Number ranges»:
manager before first use, platform_admin only after). A Betrieb sees the
ranges of the modules it holds. Numbers are never handed out here — modules
draw them through `platform.public.allocate_number` (ADR-109).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.errors import ForbiddenError, NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.organisation.models.number_range import NumberRange, NumberRangeDocumentType
from app.platform.organisation.schemas.number_range import (
    NumberRangeConfirmationRead,
    NumberRangeList,
    NumberRangePreviewRead,
    NumberRangePreviewRequest,
    NumberRangeRead,
    NumberRangeUpdate,
)
from app.platform.organisation.services import number_range as number_range_service
from app.platform.organisation.services.dealership import get_dealership_or_404
from app.platform.organisation.services.entitlement import list_active_modules

router = APIRouter(tags=["number-ranges"], route_class=IdempotentRoute)


def _read(db: Session, row: NumberRange) -> NumberRangeRead:
    return NumberRangeRead(
        document_type=row.document_type,
        module=number_range_service.MODULE_OF[row.document_type],
        pattern=row.pattern,
        start_value=row.start_value,
        yearly_reset=row.yearly_reset,
        last_value=row.last_value,
        next_number=number_range_service.next_number_example(db, row),
        frozen_at=row.frozen_at,
        version=row.version,
    )


def _modules(db: Session, dealership_id: uuid.UUID) -> list[ModuleCode]:
    return [ModuleCode(code) for code in list_active_modules(db, dealership_id)]


def _require_held(db: Session, dealership_id: uuid.UUID, document_type: NumberRangeDocumentType) -> None:
    """A range of a module the Betrieb does not hold is not there (404)."""

    if number_range_service.MODULE_OF[document_type] not in _modules(db, dealership_id):
        raise NotFoundError(f"Number range {document_type.value} was not found.")


@router.get("/dealerships/{dealership_id}/number-ranges", response_model=NumberRangeList)
def list_number_ranges(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("number_ranges")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    rows = number_range_service.list_ranges(db, dealership_id=dealership_id, modules=_modules(db, dealership_id))
    confirmation = number_range_service.latest_confirmation(db, dealership_id)
    return NumberRangeList(
        items=[_read(db, row) for row in rows],
        may_change_frozen=AccessRole.PLATFORM_ADMIN in principal.roles,
        confirmed_at=confirmation.confirmed_at if confirmation else None,
    )


@router.post("/dealerships/{dealership_id}/number-range-previews", response_model=NumberRangePreviewRead)
def preview_number_range(
    dealership_id: uuid.UUID,
    body: NumberRangePreviewRequest,
    principal: Principal = Depends(require_read("number_ranges")),
    db: Session = Depends(get_db),
):
    """The edit dialog's live example: the next number as if the edit were
    saved, or the 422 the save would answer. Writes nothing."""

    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    _require_held(db, dealership_id, body.document_type)
    example = number_range_service.preview(
        db,
        dealership_id=dealership_id,
        document_type=body.document_type,
        pattern=body.pattern,
        start_value=body.start_value,
        yearly_reset=body.yearly_reset,
    )
    db.rollback()  # a range seeded for the probe is not kept
    return NumberRangePreviewRead(next_number=example)


@router.patch("/dealerships/{dealership_id}/number-ranges/{document_type}", response_model=NumberRangeRead)
def update_number_range(
    dealership_id: uuid.UUID,
    document_type: NumberRangeDocumentType,
    body: NumberRangeUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("number_ranges")),
    db: Session = Depends(get_db),
):
    """Before the range froze: the manager (or platform staff). After: a
    platform admin only, with `reason` (409 `number_range_frozen` for anyone
    else, 422 `number_range_reason_required` without one). Audited with
    before and after."""

    require_tenant_match(dealership_id, principal)
    get_dealership_or_404(db, dealership_id)
    _require_held(db, dealership_id, document_type)
    row = number_range_service.get_or_seed(db, dealership_id=dealership_id, document_type=document_type, lock=True)
    check_version(row.version, if_match, entity_name="NumberRange")
    number_range_service.update_range(
        db,
        row=row,
        pattern=body.pattern,
        start_value=body.start_value,
        yearly_reset=body.yearly_reset,
        reason=body.reason,
        actor_id=principal.user_id,
        actor_is_platform_admin=AccessRole.PLATFORM_ADMIN in principal.roles,
    )
    db.commit()
    db.refresh(row)
    return _read(db, row)


@router.post(
    "/dealerships/{dealership_id}/number-range-confirmations",
    response_model=NumberRangeConfirmationRead,
    status_code=201,
)
def confirm_number_ranges(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """KAN-214 (D-A-21): «Nummernkreise bestätigen» — the manager confirms the
    ranges as the screen shows them, which completes the setup checklist's
    item. Freezes nothing (the ranges freeze at activation, A-RULE-3).
    Audited. The manager's own act: 403 for a colleague and for staff."""

    require_tenant_match(dealership_id, principal)
    if AccessRole.PLATFORM_ADMIN in principal.roles or not principal.is_dealer_manager:
        raise ForbiddenError("Only a manager of this Betrieb does this.", details={"reason": "manager_only"})
    get_dealership_or_404(db, dealership_id)
    row = number_range_service.confirm_ranges(db, dealership_id=dealership_id, actor_id=principal.user_id)
    db.commit()
    return NumberRangeConfirmationRead(confirmed_at=row.confirmed_at, confirmed_by=row.confirmed_by)
