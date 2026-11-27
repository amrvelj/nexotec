"""The workshop order's endpoints (KAN-810; D1 order list, D2 new order, D3 the
order with its positions, overview and history, D13 position, D16 cancel; D8
the customer's answer, KAN-811).

Every route sits behind the `aftersales` module switch, router-level, so a
Betrieb without the module gets 404 for everybody before any role check
(D-A-04, check 42), and behind the matrix row `service_orders`: aftersales,
parts, finance and the auditor read, aftersales writes, the manager does
everything (ruling 180). The tenant is the token's (rule 7). The order is the
aggregate: every change takes `If-Match` with the order's version and
answers the order as it is now. A route decides no rule: it calls the
services.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.aftersales.schemas.service_order import (
    ArrivalCreate,
    HistoryRead,
    JobCreate,
    JobUpdate,
    LabourRateProposalRead,
    PositionCreate,
    PositionUpdate,
    ServiceOrderAnswersCreate,
    ServiceOrderCancel,
    ServiceOrderCreate,
    ServiceOrderPage,
    ServiceOrderPartiesUpdate,
    ServiceOrderRead,
)
from app.aftersales.schemas.service_package import JobPackageCopy
from app.aftersales.services import answers, order_read, positions, service_packages, stays
from app.aftersales.services import service_order as service
from app.core.auth import Principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.db import get_db

router = APIRouter(
    tags=["aftersales"],
    route_class=IdempotentRoute,
    dependencies=[Depends(require_entitlement(ModuleCode.AFTERSALES))],
)
settings = get_settings()

# Check 6 (ruling 49): a change of an accepted job while somebody is clocked on it is
# refused with `service_order_job_clocked_confirm`, naming the people, until it is sent
# again confirmed — the SPA's «ask first». POST and PATCH carry `confirmWhileClocked`
# in the body (an idempotent POST stores its body, never a query); DELETE, which has no
# body, as this query parameter.
_CONFIRM = Query(default=False, alias="confirmWhileClocked")

_READ = "service_orders"
_SORT_FIELDS: dict[str, object] = {
    "orderNumber": ServiceOrder.order_number,
    "orderDate": ServiceOrder.order_date,
    "createdAt": ServiceOrder.created_at,
}
_DEFAULT_SORT = [SortField(api_name="createdAt", column=ServiceOrder.created_at, direction="desc", nullable=False)]


def _locked(db: Session, principal: Principal, order_id: uuid.UUID, if_match: int) -> ServiceOrder:
    order = service.get_order_or_404(db, tenant_id=principal.tenant_id, order_id=order_id, lock=True)
    check_version(order.version, if_match, entity_name="Service order")
    return order


def _read(db: Session, principal: Principal, order_id: uuid.UUID) -> ServiceOrderRead:
    order = service.get_order_or_404(db, tenant_id=principal.tenant_id, order_id=order_id)
    return order_read.order_read(db, order=order, group_id=principal.group_id)


@router.get("/service-orders", response_model=ServiceOrderPage)
def list_service_orders(
    chip: str | None = Query(default=None, description="waiting_for_answer|ready_for_billing"),
    state: ServiceOrderState | None = Query(default=None),
    q: str | None = None,
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read(_READ)),
    db: Session = Depends(get_db),
):
    if chip is not None and chip not in service.CHIPS:
        raise BadRequestError(f"Unknown chip '{chip}'.", details={"chips": list(service.CHIPS)})
    sort_fields = parse_sort(sort, allowed=_SORT_FIELDS) or _DEFAULT_SORT
    params = SortPageParams(limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields)
    rows, next_cursor, total, total_is_estimate = service.list_orders(
        db, tenant_id=principal.tenant_id, chip=chip, state=state, q=q, params=params
    )
    return ServiceOrderPage(
        items=order_read.summaries(db, rows), next_cursor=next_cursor, total=total, total_is_estimate=total_is_estimate
    )


@router.post("/service-orders", response_model=ServiceOrderRead, status_code=201)
def create_service_order(
    body: ServiceOrderCreate,
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = service.create_order(
        db,
        tenant_id=principal.tenant_id,
        group_id=principal.group_id,
        actor_id=principal.user_id,
        location_id=body.location_id,
        contact_id=body.contact_id,
        invoice_recipient_contact_id=body.invoice_recipient_contact_id,
        driver_contact_id=body.driver_contact_id,
        vehicle_id=body.vehicle_id,
    )
    return order_read.order_read(db, order=order, group_id=principal.group_id)


@router.get("/service-orders/{order_id}", response_model=ServiceOrderRead)
def get_service_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_read(_READ)),
    db: Session = Depends(get_db),
):
    return _read(db, principal, order_id)


@router.patch("/service-orders/{order_id}", response_model=ServiceOrderRead)
def change_service_order_parties(
    order_id: uuid.UUID,
    body: ServiceOrderPartiesUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    # KAN-837: the new contacts' merge locks before the order's row lock, the order
    # aftersales.contact_merged takes them in.
    service.lock_order_contacts(db, changes.values())
    order = _locked(db, principal, order_id, if_match)
    service.change_parties(db, order=order, group_id=principal.group_id, actor_id=principal.user_id, changes=changes)
    return _read(db, principal, order_id)


@router.post("/service-orders/{order_id}/cancel", response_model=ServiceOrderRead)
def cancel_service_order(
    order_id: uuid.UUID,
    body: ServiceOrderCancel,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    service.cancel_order(db, order=order, actor_id=principal.user_id, reason=body.reason)
    return _read(db, principal, order_id)


@router.get("/service-orders/{order_id}/history", response_model=HistoryRead)
def get_service_order_history(
    order_id: uuid.UUID,
    principal: Principal = Depends(require_read(_READ)),
    db: Session = Depends(get_db),
):
    order = service.get_order_or_404(db, tenant_id=principal.tenant_id, order_id=order_id)
    return order_read.history(db, order=order, group_id=principal.group_id)


@router.get("/service-orders/{order_id}/labour-rate-proposal", response_model=LabourRateProposalRead)
def get_labour_rate_proposal(
    order_id: uuid.UUID,
    labour_category_id: uuid.UUID = Query(alias="labourCategoryId"),
    principal: Principal = Depends(require_read(_READ)),
    db: Session = Depends(get_db),
):
    """D13: the rate a labour position of this category would take (check 11), and
    the unit its time is typed in here (ruling 183)."""

    order = service.get_order_or_404(db, tenant_id=principal.tenant_id, order_id=order_id)
    proposal = positions.propose_rate(db, order=order, category_id=labour_category_id)
    return order_read.proposal_read(proposal, positions.time_unit_in_force(db, order=order))


# --- the customer's answer (KAN-811) -----------------------------------------------------------


@router.post("/service-orders/{order_id}/answers", response_model=ServiceOrderRead, status_code=201)
def record_answers(
    order_id: uuid.UUID,
    body: ServiceOrderAnswersCreate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    """D8 «Antwort erfassen»: the customer's answer to one or more jobs, and how it came."""

    order = _locked(db, principal, order_id, if_match)
    answers.record_answers(
        db,
        order=order,
        actor_id=principal.user_id,
        channel=body.channel,
        answers=[
            answers.JobAnswerInput(job_id=e.job_id, result=e.result, note=e.note, hours_billed=e.hours_billed)
            for e in body.jobs
        ],
    )
    return _read(db, principal, order_id)


# --- the stay of the car (KAN-813) ---------------------------------------------------------------


@router.post("/service-orders/{order_id}/arrival", response_model=ServiceOrderRead, status_code=201)
def receive_car(
    order_id: uuid.UUID,
    body: ArrivalCreate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    """D14 «Fahrzeug ist da»: the car of the order is here (check 22-24)."""

    unlocked = service.get_order_or_404(db, tenant_id=principal.tenant_id, order_id=order_id)
    # The car's stay lock before any order row (the arrival also takes the car's other open orders).
    service.lock_stay_car(db, tenant_id=principal.tenant_id, vehicle_id=unlocked.vehicle_id)
    order = _locked(db, principal, order_id, if_match)
    stays.receive_car(
        db, order=order, actor_id=principal.user_id,
        data=stays.ArrivalInput(mileage=body.mileage, key_tag=body.key_tag, parking_place=body.parking_place,
                                remark=body.remark),
    )
    return _read(db, principal, order_id)


# --- jobs -------------------------------------------------------------------------------------


@router.post("/service-orders/{order_id}/jobs", response_model=ServiceOrderRead, status_code=201)
def add_job(
    order_id: uuid.UUID,
    body: JobCreate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    positions.add_job(db, order=order, actor_id=principal.user_id, name=body.name,
                      billing_category=body.billing_category)
    return _read(db, principal, order_id)


@router.patch("/service-orders/{order_id}/jobs/{job_id}", response_model=ServiceOrderRead)
def rename_job(
    order_id: uuid.UUID,
    job_id: uuid.UUID,
    body: JobUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    job = positions.get_job_or_404(db, order=order, job_id=job_id)
    positions.rename_job(db, order=order, job=job, actor_id=principal.user_id, name=body.name)
    return _read(db, principal, order_id)


@router.delete("/service-orders/{order_id}/jobs/{job_id}", response_model=ServiceOrderRead)
def delete_job(
    order_id: uuid.UUID,
    job_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    job = positions.get_job_or_404(db, order=order, job_id=job_id)
    positions.delete_job(db, order=order, job=job, actor_id=principal.user_id)
    return _read(db, principal, order_id)


# --- positions ----------------------------------------------------------------------------------


@router.post("/service-orders/{order_id}/jobs/{job_id}/positions", response_model=ServiceOrderRead, status_code=201)
def add_position(
    order_id: uuid.UUID,
    job_id: uuid.UUID,
    body: PositionCreate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    job = positions.get_job_or_404(db, order=order, job_id=job_id)
    positions.add_position(
        db,
        order=order,
        job=job,
        actor_id=principal.user_id,
        kind=body.kind,
        data=positions.PositionInput(
            text=body.text,
            labour_category_id=body.labour_category_id,
            time=body.time,
            time_unit=body.time_unit,
            unit_price=body.unit_price,
            price_entered=body.price_entered,
        ),
        confirmed=body.confirm_while_clocked,
    )
    return _read(db, principal, order_id)


@router.post(
    "/service-orders/{order_id}/jobs/{job_id}/service-packages", response_model=ServiceOrderRead, status_code=201
)
def add_service_package(
    order_id: uuid.UUID,
    job_id: uuid.UUID,
    body: JobPackageCopy,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    """D13 «from the catalogue» (KAN-818, check 44): the package's positions copied into the job."""

    order = _locked(db, principal, order_id, if_match)
    job = positions.get_job_or_404(db, order=order, job_id=job_id)
    service_packages.copy_package_onto_job(
        db, order=order, job=job, package_id=body.service_package_id, actor_id=principal.user_id,
        confirmed=body.confirm_while_clocked,
    )
    return _read(db, principal, order_id)


@router.patch("/service-orders/{order_id}/positions/{position_id}", response_model=ServiceOrderRead)
def change_position(
    order_id: uuid.UUID,
    position_id: uuid.UUID,
    body: PositionUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    position = positions.get_position_or_404(db, order=order, position_id=position_id)
    sent = body.model_fields_set
    positions.change_position(
        db,
        order=order,
        position=position,
        actor_id=principal.user_id,
        data=positions.PositionInput(
            text=body.text,
            labour_category_id=body.labour_category_id,
            time=body.time,
            time_unit=body.time_unit,
            unit_price=body.unit_price,
            price_entered=body.price_entered,
            category_set="labour_category_id" in sent,
            time_set="time" in sent,
            price_set="unit_price" in sent,
            entered_set="price_entered" in sent,
            unit_set="time_unit" in sent,
        ),
        confirmed=body.confirm_while_clocked,
    )
    return _read(db, principal, order_id)


@router.delete("/service-orders/{order_id}/positions/{position_id}", response_model=ServiceOrderRead)
def delete_position(
    order_id: uuid.UUID,
    position_id: uuid.UUID,
    confirm_while_clocked: bool = _CONFIRM,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write(_READ)),
    db: Session = Depends(get_db),
):
    order = _locked(db, principal, order_id, if_match)
    position = positions.get_position_or_404(db, order=order, position_id=position_id)
    positions.delete_position(db, order=order, position=position, actor_id=principal.user_id,
                              confirmed=confirm_while_clocked)
    return _read(db, principal, order_id)
