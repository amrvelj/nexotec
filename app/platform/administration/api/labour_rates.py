"""Labour rates in Dealer Administration (KAN-806; D-A-19; rulings 177, 178).

The Betrieb's categories with their rates per location and their people,
and the fixed rates agreed with contacts; «which rate applies here» as a
read. Matrix row `labour_rates`: aftersales, parts, finance and the manager
read, the manager writes. Every route is the Betrieb's (tenant match, the
platform tenant 404) and needs the `aftersales` module, else 404 (D-A-04) —
answered here, not as a router gate, since a router-level entitlement gate is
the sales routers' alone. The role check runs first, so a role without the
matrix row gets 403 before the 404: it depends on the role alone and
confirms nothing about the Betrieb (the settings API's order).

Creating an agreement needs the contact, which contact owns: that one route
lives at the composition root (`app/api/v1/labour_rate_agreements.py`).
O16, O18 and O19 are written through the settings API (`area_settings.py`,
area `labour_rates`).
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams, paginate_query_sorted
from app.core.permissions import ModuleCode, require_read, require_write
from app.core.sorting import build_page_for_sort, decode_cursor_for_sort, parse_sort
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.administration.schemas.labour_rates import (
    LabourCategoryMemberCreate,
    LabourCategoryMemberRead,
    LabourRateAgreementCreate,
    LabourRateAgreementPage,
    LabourRateAgreementRead,
    LabourRateAnswerRead,
    LabourRateCategoryCreate,
    LabourRateCategoryList,
    LabourRateCategoryRead,
    LabourRateCategoryUpdate,
    LabourRateCreate,
    LabourRateRead,
)
from app.platform.identity.services import user_labour_category as members_service
from app.platform.organisation.models.labour_rate import LabourRate, LabourRateAgreement, LabourRateCategory
from app.platform.organisation.services import labour_rates as service
from app.platform.organisation.services.dealership import get_dealer_dealership_or_404
from app.platform.organisation.services.entitlement import list_active_modules
from app.platform.organisation.services.labour_rate_answer import labour_rate_for

router = APIRouter(tags=["labour-rates"], route_class=IdempotentRoute)
settings = get_settings()

AGREEMENT_SORT = "validFrom:desc"


def guard(db: Session, dealership_id: uuid.UUID, principal: Principal) -> None:
    """The Betrieb's own, a dealer Betrieb, holding Aftersales — else 404."""

    require_tenant_match(dealership_id, principal)
    get_dealer_dealership_or_404(db, dealership_id)
    if ModuleCode.AFTERSALES.value not in list_active_modules(db, dealership_id):
        raise NotFoundError("Labour rates were not found.")


def _rate_read(rate: LabourRate, statuses: dict) -> LabourRateRead:
    return LabourRateRead(
        id=rate.id,
        location_id=rate.location_id,
        valid_from=rate.valid_from,
        amount=rate.amount,
        entered=rate.entered,
        status=statuses[rate.id],
        withdrawn_at=rate.withdrawn_at,
        version=rate.version,
        created_at=rate.created_at,
        created_by=rate.created_by,
    )


def _category_reads(db: Session, dealership_id: uuid.UUID, categories: list[LabourRateCategory]) -> list:
    on = service.today()
    ids = [category.id for category in categories]
    rate_rows = service.rates_of(db, dealership_id=dealership_id, category_ids=ids)
    statuses = service.rate_statuses(rate_rows, on)
    members = members_service.members_of(db, dealership_id=dealership_id, category_ids=ids)
    by_category: dict[uuid.UUID, list[LabourRateRead]] = {category_id: [] for category_id in ids}
    for rate in rate_rows:
        by_category[rate.category_id].append(_rate_read(rate, statuses))
    return [
        LabourRateCategoryRead(
            id=category.id,
            name=category.name,
            name_translations=category.name_translations or {},
            is_active=category.is_active,
            version=category.version,
            created_at=category.created_at,
            rates=by_category[category.id],
            members=[
                LabourCategoryMemberRead(user_id=m.user_id, display_name=m.display_name, is_active=m.is_active)
                for m in members[category.id]
            ],
        )
        for category in categories
    ]


def _one_category(db: Session, dealership_id: uuid.UUID, category_id: uuid.UUID) -> LabourRateCategoryRead:
    category = service.get_category_or_404(db, dealership_id=dealership_id, category_id=category_id)
    return _category_reads(db, dealership_id, [category])[0]


def agreement_read(row: LabourRateAgreement, status) -> LabourRateAgreementRead:
    return LabourRateAgreementRead(
        id=row.id,
        contact_id=row.contact_id,
        contact_label=row.contact_label,
        category_id=row.category_id,
        kind=row.kind,
        valid_from=row.valid_from,
        amount=row.amount,
        entered=row.entered,
        status=status,
        withdrawn_at=row.withdrawn_at,
        version=row.version,
        created_at=row.created_at,
    )


def agreement_status(db: Session, row: LabourRateAgreement):
    siblings = service.agreements_of(
        db, dealership_id=row.dealership_id, contact_id=row.contact_id, category_id=row.category_id,
        include_withdrawn=True,
    )
    return service.agreement_statuses(siblings, service.today())[row.id]


# --- categories, rates, members ------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/labour-rate-categories", response_model=LabourRateCategoryList)
def list_labour_rate_categories(
    dealership_id: uuid.UUID,
    include_inactive: bool = Query(default=True, alias="includeInactive"),
    principal: Principal = Depends(require_read("labour_rates")),
    db: Session = Depends(get_db),
):
    """Every category with its rates (newest start first, each with its status
    today) and its people. A Betrieb has a handful of categories: one page."""

    guard(db, dealership_id, principal)
    categories = service.list_categories(db, dealership_id=dealership_id, include_inactive=include_inactive)
    return LabourRateCategoryList(today=service.today(), items=_category_reads(db, dealership_id, categories))


@router.post(
    "/dealerships/{dealership_id}/labour-rate-categories", response_model=LabourRateCategoryRead, status_code=201
)
def create_labour_rate_category(
    dealership_id: uuid.UUID,
    body: LabourRateCategoryCreate,
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    guard(db, dealership_id, principal)
    category = service.create_category(
        db, dealership_id=dealership_id, name=body.name, name_translations=body.name_translations,
        actor_id=principal.user_id,
    )
    db.commit()
    return _one_category(db, dealership_id, category.id)


@router.patch(
    "/dealerships/{dealership_id}/labour-rate-categories/{category_id}", response_model=LabourRateCategoryRead
)
def update_labour_rate_category(
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    body: LabourRateCategoryUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    """Renames, translates, deactivates or reactivates a category; its rates stay."""

    guard(db, dealership_id, principal)
    fields = body.model_fields_set
    service.change_category(
        db,
        dealership_id=dealership_id,
        category_id=category_id,
        expected_version=if_match,
        actor_id=principal.user_id,
        name=body.name,
        is_active=body.is_active,
        **({"name_translations": body.name_translations} if "name_translations" in fields else {}),
    )
    db.commit()
    return _one_category(db, dealership_id, category_id)


@router.post(
    "/dealerships/{dealership_id}/labour-rate-categories/{category_id}/rates",
    response_model=LabourRateCategoryRead,
    status_code=201,
)
def create_labour_rate(
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    body: LabourRateCreate,
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    """A new rate of the category at a location from a date, today or later
    (ruling 178). There is no edit: a new price is a new rate."""

    guard(db, dealership_id, principal)
    service.create_rate(
        db, dealership_id=dealership_id, category_id=category_id, location_id=body.location_id,
        valid_from=body.valid_from, amount=body.amount, entered=body.entered, actor_id=principal.user_id,
    )
    db.commit()
    return _one_category(db, dealership_id, category_id)


@router.post("/dealerships/{dealership_id}/labour-rates/{rate_id}/withdraw", response_model=LabourRateCategoryRead)
def withdraw_labour_rate(
    dealership_id: uuid.UUID,
    rate_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    """Withdraws a rate that has not started yet; one in use stays (ruling 178)."""

    guard(db, dealership_id, principal)
    rate = service.withdraw_rate(
        db, dealership_id=dealership_id, rate_id=rate_id, expected_version=if_match, actor_id=principal.user_id
    )
    db.commit()
    return _one_category(db, dealership_id, rate.category_id)


@router.post(
    "/dealerships/{dealership_id}/labour-rate-categories/{category_id}/members",
    response_model=LabourRateCategoryRead,
    status_code=201,
)
def add_labour_category_member(
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    body: LabourCategoryMemberCreate,
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    guard(db, dealership_id, principal)
    members_service.allocate_user(
        db, dealership_id=dealership_id, category_id=category_id, user_id=body.user_id, actor_id=principal.user_id
    )
    db.commit()
    return _one_category(db, dealership_id, category_id)


@router.delete(
    "/dealerships/{dealership_id}/labour-rate-categories/{category_id}/members/{user_id}", status_code=204
)
def remove_labour_category_member(
    dealership_id: uuid.UUID,
    category_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    guard(db, dealership_id, principal)
    members_service.remove_user(
        db, dealership_id=dealership_id, category_id=category_id, user_id=user_id, actor_id=principal.user_id
    )
    db.commit()
    return Response(status_code=204)


# --- agreements ------------------------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/labour-rate-agreements", response_model=LabourRateAgreementPage)
def list_labour_rate_agreements(
    dealership_id: uuid.UUID,
    contact_id: uuid.UUID | None = Query(default=None, alias="contactId"),
    category_id: uuid.UUID | None = Query(default=None, alias="categoryId"),
    include_withdrawn: bool = Query(default=False, alias="includeWithdrawn"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read("labour_rates")),
    db: Session = Depends(get_db),
):
    """The agreed rates, latest start first (cursor pagination: insurers and
    fleets make this list grow)."""

    guard(db, dealership_id, principal)
    sort_fields = parse_sort(AGREEMENT_SORT, allowed={"validFrom": LabourRateAgreement.valid_from})
    params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)
    stmt = service.agreements_query(
        dealership_id=dealership_id, contact_id=contact_id, category_id=category_id,
        include_withdrawn=include_withdrawn,
    )
    rows = list(db.scalars(paginate_query_sorted(stmt, model=LabourRateAgreement, params=params)).all())
    page, next_cursor = build_page_for_sort(rows, params)
    # One read for every contact and category on the page: a row's status depends on its siblings.
    on = service.today()
    pairs = {(row.contact_id, row.category_id) for row in page}
    siblings = [
        row
        for row in service.agreements_of(
            db,
            dealership_id=dealership_id,
            contact_ids=[contact for contact, _ in pairs],
            include_withdrawn=True,
        )
        if (row.contact_id, row.category_id) in pairs
    ]
    statuses = service.agreement_statuses(siblings, on)
    return LabourRateAgreementPage(
        today=on, items=[agreement_read(row, statuses[row.id]) for row in page], next_cursor=next_cursor
    )


@router.post(
    "/dealerships/{dealership_id}/labour-rate-agreements/{agreement_id}/withdraw",
    response_model=LabourRateAgreementRead,
)
def withdraw_labour_rate_agreement(
    dealership_id: uuid.UUID,
    agreement_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("labour_rates")),
    db: Session = Depends(get_db),
):
    """Withdraws an agreement row that has not started yet (ruling 178)."""

    guard(db, dealership_id, principal)
    row = service.withdraw_agreement(
        db, dealership_id=dealership_id, agreement_id=agreement_id, expected_version=if_match,
        actor_id=principal.user_id,
    )
    db.commit()
    db.refresh(row)
    return agreement_read(row, agreement_status(db, row))


# --- the question ----------------------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/labour-rate-answer", response_model=LabourRateAnswerRead)
def get_labour_rate_answer(
    dealership_id: uuid.UUID,
    category_id: uuid.UUID = Query(alias="categoryId"),
    location_id: uuid.UUID = Query(alias="locationId"),
    on: dt.date | None = Query(default=None),
    invoice_recipient_contact_id: uuid.UUID | None = Query(default=None, alias="invoiceRecipientContactId"),
    principal: Principal = Depends(require_read("labour_rates")),
    db: Session = Depends(get_db),
):
    """The rate for a category at a location on a day (today when not given)
    for an invoice recipient: an agreed rate first, else the category rate,
    else `found: false` — an answer, never an error (check 11)."""

    guard(db, dealership_id, principal)
    answer = labour_rate_for(
        db, dealership_id=dealership_id, category_id=category_id, location_id=location_id,
        on=on or service.today(), invoice_recipient_contact_id=invoice_recipient_contact_id,
    )
    return LabourRateAnswerRead(
        category_id=answer.category_id,
        found=answer.found,
        source=answer.source,
        rate_id=answer.rate_id,
        amount=answer.amount,
        entered=answer.entered,
        valid_from=answer.valid_from,
        category_is_active=answer.category_is_active,
        missing=answer.missing,
    )


# --- the agreement POST's platform half ---------------------------------------------------------


def create_labour_rate_agreement(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    principal: Principal,
    contact_label: str,
    body: LabourRateAgreementCreate,
) -> LabourRateAgreementRead:
    """Called by the composition root once it resolved the contact in the
    Betrieb's group (contact is above platform, ADR-131). Commits."""

    row = service.create_agreement(
        db,
        dealership_id=dealership_id,
        contact=service.AgreementContact(id=body.contact_id, label=contact_label),
        category_id=body.category_id,
        kind=body.kind,
        valid_from=body.valid_from,
        amount=body.amount,
        entered=body.entered,
        actor_id=principal.user_id,
    )
    db.commit()
    db.refresh(row)
    return agreement_read(row, agreement_status(db, row))
