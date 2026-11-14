"""Valuation endpoints (WP-8 PR-5)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams
from app.core.permissions import ModuleCode, require_entitlement, require_write
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.db import get_db
from app.valuation.models.valuation import Valuation, ValuationProviderQuote
from app.valuation.schemas.valuation import (
    DeductionRead,
    ProviderQuoteRead,
    ProviderQuoteRequest,
    ValuationCreate,
    ValuationPage,
    ValuationRead,
    ValuationRevertUse,
)
from app.valuation.services import provider_quote as provider_quote_service
from app.valuation.services import valuation as valuation_service
from app.valuation.services.contract_carry import valuations_carried_by_signed_contracts

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(tags=["valuation"], route_class=IdempotentRoute, dependencies=[Depends(require_entitlement(ModuleCode.SALES))])
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


def _valuation_read(
    db: Session,
    valuation: Valuation,
    *,
    has_signed_contract: bool,
    quotes: dict[uuid.UUID, ValuationProviderQuote],
) -> ValuationRead:
    """`status` is derived, never a column on `Valuation` (ADR-066) — so it
    cannot be produced by validating straight off the ORM object the way
    every other field can (KAN-65: that used to be exactly what this did,
    and `ValuationRead.model_validate(valuation, from_attributes=True)`
    raised a `ValidationError` — status missing — for every single
    request, on every backend, because no existing test exercised this
    HTTP response path). Assemble the dict by hand instead, with the
    derived fields filled in before validation ever runs.
    """

    deductions = valuation_service.get_deductions(db, valuation.id)
    derived = ("status", "deductions", "has_signed_contract", "provider_quote")
    fields = {name: getattr(valuation, name) for name in ValuationRead.model_fields if name not in derived}
    fields["status"] = valuation_service.derive_status(valuation)
    fields["deductions"] = [DeductionRead.model_validate(d, from_attributes=True) for d in deductions]
    fields["has_signed_contract"] = has_signed_contract
    fields["provider_quote"] = (
        _quote_read(quotes.get(valuation.provider_quote_id)) if valuation.provider_quote_id is not None else None
    )
    return ValuationRead.model_validate(fields)


def _quote_read(quote: ValuationProviderQuote | None) -> ProviderQuoteRead | None:
    return ProviderQuoteRead.model_validate(quote, from_attributes=True) if quote is not None else None


def _valuation_reads(db: Session, tenant_id: uuid.UUID, valuations: list[Valuation]) -> list[ValuationRead]:
    """KAN-115 — one read for the whole page, not one per row; valuation's
    own carry rows since KAN-631 (ADR-131), never a call into Sales."""

    carried = valuations_carried_by_signed_contracts(
        db, tenant_id=tenant_id, valuation_ids=[valuation.id for valuation in valuations]
    )
    # KAN-552: the page's provider quotes in one read, not one per row.
    quote_ids = {v.provider_quote_id for v in valuations if v.provider_quote_id is not None}
    quotes = provider_quote_service.quotes_by_id(db, tenant_id=tenant_id, quote_ids=quote_ids)
    return [
        _valuation_read(db, valuation, has_signed_contract=valuation.id in carried, quotes=quotes)
        for valuation in valuations
    ]


@router.post("/valuations", response_model=ValuationRead, status_code=201)
def create_valuation(
    body: ValuationCreate,
    principal: Principal = Depends(require_write("valuations")),
    db: Session = Depends(get_db),
):
    valuation = valuation_service.create_valuation(
        db, tenant_id=principal.tenant_id, group_id=principal.group_id, data=body, actor_id=principal.user_id
    )
    return _valuation_reads(db, principal.tenant_id, [valuation])[0]


@router.post("/valuations/provider-quotes", response_model=ProviderQuoteRead, status_code=201)
def request_provider_quote(
    body: ProviderQuoteRequest,
    principal: Principal = Depends(require_write("valuations")),
    db: Session = Depends(get_db),
):
    """KAN-552 — «Bewertung von auto-i-dat holen»: the identAPI's valuation
    and residual table for a configured car, stored as a quote the valuation
    created next references. The advisor's deliberate action; nothing calls
    it on opening a screen."""

    quote = provider_quote_service.request_provider_quote(
        db, tenant_id=principal.tenant_id, actor_id=principal.user_id, data=body
    )
    return _quote_read(quote)


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
        limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields
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
        items=_valuation_reads(db, principal.tenant_id, rows),
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
    return _valuation_reads(db, principal.tenant_id, [valuation])[0]


@router.post("/valuations/{valuation_id}/mark-used", response_model=ValuationRead)
def mark_valuation_used(
    valuation_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("valuations")),
    db: Session = Depends(get_db),
):
    """«Als verwendet markieren» — a repair, only for a valuation a signed
    contract carries (KAN-115; 409 `no_signed_contract` otherwise). Sales's
    contract confirmation stamps a trade-in valuation through
    app.valuation.public.consume_valuation_for_contract (KAN-101), not
    through this HTTP endpoint.
    """

    valuation = valuation_service.get_valuation_or_404(db, principal.tenant_id, valuation_id)
    check_version(valuation.version, if_match, entity_name="Valuation")
    valuation = valuation_service.mark_used_by_hand(db, valuation=valuation, actor_id=principal.user_id)
    return _valuation_reads(db, principal.tenant_id, [valuation])[0]


@router.post("/valuations/{valuation_id}/revert-use", response_model=ValuationRead)
def revert_valuation_use(
    valuation_id: uuid.UUID,
    body: ValuationRevertUse,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("valuation_use_reversal")),
    db: Session = Depends(get_db),
):
    """«Verwendung aufheben» (KAN-253, Anto 2026-10-31 option C) — the dealer
    manager clears a «Verwendet» stamp no signed contract stands behind,
    with a stated reason (audited). 409 `signed_contract_carries_it` while a
    signed contract of this dealership carries it, 409 `valuation_not_used` when it is
    not stamped, 409 `stamp_too_recent` within the confirmation grace, and 409
    `pending_contract_carries_it` while a pending contract of this dealership
    carries it (KAN-520: cancel that contract first).
    """

    valuation = valuation_service.get_valuation_or_404(db, principal.tenant_id, valuation_id)
    check_version(valuation.version, if_match, entity_name="Valuation")
    valuation = valuation_service.revert_use_by_hand(
        db, valuation=valuation, actor_id=principal.user_id, reason=body.reason
    )
    return _valuation_reads(db, principal.tenant_id, [valuation])[0]
