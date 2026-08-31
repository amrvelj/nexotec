"""Valuation endpoints (WP-8 PR-5)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import require_write
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.valuation.models.valuation import Valuation
from app.valuation.schemas.valuation import DeductionRead, ValuationCreate, ValuationPage, ValuationRead
from app.valuation.services import valuation as valuation_service

router = APIRouter(tags=["valuation"])
settings = get_settings()

# U-03 — status is deliberately ABSENT from this allow-list: it is derived
# on read, never a real column, so it cannot back a sortable index.
VALUATION_SORT_FIELDS: dict[str, object] = {
    "valuationNumber": Valuation.valuation_number,
    "validUntil": Valuation.valid_until,
    "mileage": Valuation.mileage,
    "finalOffer": Valuation.final_offer,
    "createdAt": Valuation.created_at,
}
_DEFAULT_VALUATION_SORT = [
    SortField(api_name="createdAt", column=Valuation.created_at, direction="desc", nullable=False)
]


def _valuation_read(db: Session, valuation: Valuation) -> ValuationRead:
    deductions = valuation_service.get_deductions(db, valuation.id)
    base = ValuationRead.model_validate(valuation, from_attributes=True)
    return base.model_copy(
        update={
            "status": valuation_service.derive_status(valuation),
            "deductions": [DeductionRead.model_validate(d, from_attributes=True) for d in deductions],
        }
    )


@router.post("/valuations", response_model=ValuationRead, status_code=201)
def create_valuation(
    body: ValuationCreate,
    principal: Principal = Depends(require_write("valuations")),
    db: Session = Depends(get_db),
):
    valuation = valuation_service.create_valuation(
        db, tenant_id=principal.tenant_id, group_id=principal.group_id, data=body, actor_id=principal.user_id
    )
    return _valuation_read(db, valuation)


@router.get("/valuations", response_model=ValuationPage)
def list_valuations(
    chip: str | None = Query(default=None, description="valid|expiring_soon|expired|unattached|mine"),
    q: str | None = None,
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=VALUATION_SORT_FIELDS) or _DEFAULT_VALUATION_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = valuation_service.list_valuations(
        db,
        tenant_id=principal.tenant_id,
        chip=chip,
        q=q,
        created_by=principal.user_id if chip == "mine" else None,
        params=params,
    )
    return ValuationPage(
        items=[_valuation_read(db, r) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/valuations/{valuation_id}", response_model=ValuationRead)
def get_valuation(
    valuation_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    valuation = valuation_service.get_valuation_or_404(db, principal.tenant_id, valuation_id)
    return _valuation_read(db, valuation)


@router.post("/valuations/{valuation_id}/mark-used", response_model=ValuationRead)
def mark_valuation_used(
    valuation_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("valuations")),
    db: Session = Depends(get_db),
):
    """Exposed for completeness/manual correction — Sales's own contract-
    confirmation flow (PR-6) calls app.valuation.public.mark_valuation_used
    directly, not this HTTP endpoint.
    """

    valuation = valuation_service.get_valuation_or_404(db, principal.tenant_id, valuation_id)
    check_version(valuation.version, if_match, entity_name="Valuation")
    valuation = valuation_service.mark_used(db, valuation=valuation, actor_id=principal.user_id)
    return _valuation_read(db, valuation)
