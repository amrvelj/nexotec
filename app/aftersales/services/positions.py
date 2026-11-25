"""Jobs and positions on the workshop order (KAN-810; build slice 1, tables 3.2
and 3.3; rulings 177, 178, 183; checks 11, 12, 47, 48).

Table 3.3, as far as this slice goes («done» and «time to bill» come with
KAN-814 and KAN-816):

| What happens | In state | Result | Refused when |
| --- | --- | --- | --- |
| Add or change | Open | The typed price leads (decision 15); up to four decimals per unit (decision 23) | The order is frozen; the job is stopped (KAN-811) |
| Delete | Open | Gone | Time was clocked on it (decision 64) |

A job (table 3.2) gets a name and the billing category; it is deleted only
while draft, waiting or declined, and never with clocked time. Recording the
customer's answer is KAN-811; `accept_job` is the state change it makes.

**Labour.** A position names its labour category. By time, the time is typed
in the Betrieb's unit at the order's location (O18/O19 through
`platform.public.labour_time_unit`) and kept with that unit and its work
units per hour (check 48); the amount is hours × the hourly price (ruling
183). The price, when none is typed, is the rate «which applies here»
(`platform.public.labour_rate_for`: the invoice recipient's agreement first,
else the category rate at the order's location on the order's day) as it was
typed in Dealer Administration, net or gross (ruling 177) — and the position
keeps where it came from (check 11). A typed price is kept as typed, net or
gross (check 12). A fixed price is always typed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    BillingCategory,
    JobAnswerState,
    LabourTimeUnit,
    PositionKind,
    RateSource,
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderPosition,
)
from app.aftersales.services import work_guard
from app.aftersales.services.service_order import (
    order_follows_accepted_job,
    record_order_change,
    refuse_frozen,
)
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.money import UNIT_PRICE_PLACES, PriceEntry
from app.platform.public import (
    LabourRateSource,
    get_dealership_or_404,
    labour_category_labels,
    labour_rate_for,
    labour_time_unit,
)

_TIME_PLACES = 2
_DELETABLE_JOB_STATES = frozenset({JobAnswerState.DRAFT, JobAnswerState.WAITING, JobAnswerState.DECLINED})
_LABOUR = frozenset({PositionKind.LABOUR_FIXED, PositionKind.LABOUR_TIME})


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


# --- jobs -----------------------------------------------------------------------------------


def get_job_or_404(db: Session, *, order: ServiceOrder, job_id: uuid.UUID) -> ServiceOrderJob:
    job = db.scalar(
        select(ServiceOrderJob).where(ServiceOrderJob.id == job_id, ServiceOrderJob.service_order_id == order.id)
    )
    if job is None:
        raise NotFoundError(f"Job {job_id} was not found.")
    return job


def _job_snapshot(job: ServiceOrderJob) -> dict[str, Any]:
    return {
        "jobId": str(job.id),
        "name": job.name,
        "billingCategory": job.billing_category.value,
        "answerState": job.answer_state.value,
    }


def _name(value: str, what: str) -> str:
    name = value.strip()
    if not name:
        raise UnprocessableEntityError(f"{what} needs a name.", details={"reason": "service_order_job_name_required"})
    return name


def add_job(
    db: Session, *, order: ServiceOrder, actor_id: uuid.UUID, name: str, billing_category: BillingCategory
) -> ServiceOrderJob:
    refuse_frozen(order)
    last = db.scalar(select(func.max(ServiceOrderJob.sort_index)).where(ServiceOrderJob.service_order_id == order.id))
    job = ServiceOrderJob(
        tenant_id=order.tenant_id,
        service_order_id=order.id,
        sort_index=(last or 0) + 1,
        name=_name(name, "A job"),
        billing_category=billing_category,
        answer_state=JobAnswerState.DRAFT,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(job)
    db.flush()
    record_order_change(db, order, action="service_order_job_added", actor_id=actor_id, after=_job_snapshot(job))
    db.commit()
    return job


def rename_job(db: Session, *, order: ServiceOrder, job: ServiceOrderJob, actor_id: uuid.UUID, name: str) -> None:
    refuse_frozen(order)
    before = _job_snapshot(job)
    job.name = _name(name, "A job")
    job.updated_by = actor_id
    if job.name != before["name"]:
        record_order_change(
            db, order, action="service_order_job_changed", actor_id=actor_id, before=before, after=_job_snapshot(job)
        )
    db.commit()


def delete_job(db: Session, *, order: ServiceOrder, job: ServiceOrderJob, actor_id: uuid.UUID) -> None:
    """Table 3.2: gone while draft, waiting or declined; never with clocked time (decision 64)."""

    refuse_frozen(order)
    if job.answer_state not in _DELETABLE_JOB_STATES:
        raise ConflictError(
            f"Job «{job.name}» has been accepted and cannot be deleted — record the customer's answer instead.",
            details={"reason": "service_order_job_accepted"},
        )
    if work_guard.time_clocked_on_job(db, tenant_id=order.tenant_id, job_id=job.id):
        raise ConflictError(
            f"Time has been clocked on job «{job.name}», so it cannot be deleted — decline it instead.",
            details={"reason": "service_order_job_time_clocked"},
        )
    before = _job_snapshot(job)
    for position in db.scalars(select(ServiceOrderPosition).where(ServiceOrderPosition.job_id == job.id)):
        db.delete(position)
    db.flush()
    db.delete(job)
    record_order_change(db, order, action="service_order_job_deleted", actor_id=actor_id, before=before)
    db.commit()


def accept_job(db: Session, *, order: ServiceOrder, job: ServiceOrderJob, actor_id: uuid.UUID) -> None:
    """The state change of «the customer accepted the job» (tables 3.1 and 3.2): the job
    is accepted, a draft order becomes accepted (check 4). KAN-811 records how the answer
    came and the amount, and gives it its route; a job may be accepted before it has a
    price (ruling 167). Commits."""

    refuse_frozen(order)
    if job.answer_state not in {JobAnswerState.DRAFT, JobAnswerState.WAITING}:
        raise ConflictError(
            f"Job «{job.name}» already has its answer.", details={"reason": "service_order_job_already_answered"}
        )
    before = _job_snapshot(job)
    job.answer_state = JobAnswerState.ACCEPTED
    job.answered_at = utcnow()
    job.answered_by = actor_id
    order_follows_accepted_job(order)
    record_order_change(
        db, order, action="service_order_job_accepted", actor_id=actor_id, before=before,
        after={**_job_snapshot(job), "orderState": order.state.value},
    )
    db.commit()


# --- the labour rate and the unit -------------------------------------------------------------


@dataclass(frozen=True)
class RateProposal:
    """The rate «which applies here» for a category on this order (check 11)."""

    category_id: uuid.UUID
    found: bool
    source: RateSource | None
    rate_id: uuid.UUID | None
    valid_from: Any
    amount: Decimal | None
    entered: PriceEntry | None
    vat_rate: Decimal | None


_SOURCE = {LabourRateSource.AGREEMENT: RateSource.AGREEMENT, LabourRateSource.CATEGORY_RATE: RateSource.CATEGORY_RATE}


def _vat_rate(db: Session, order: ServiceOrder) -> Decimal | None:
    return get_dealership_or_404(db, order.tenant_id).vat_rate


def _category_label(db: Session, order: ServiceOrder, category_id: uuid.UUID) -> str:
    label = labour_category_labels(db, dealership_id=order.tenant_id, category_ids=[category_id]).get(category_id)
    if label is None:
        raise UnprocessableEntityError(
            "This labour category is not one of this business's.",
            details={"reason": "service_order_labour_category_unknown"},
        )
    return label.name[:200]


def propose_rate(db: Session, *, order: ServiceOrder, category_id: uuid.UUID) -> RateProposal:
    _category_label(db, order, category_id)
    answer = labour_rate_for(
        db,
        dealership_id=order.tenant_id,
        category_id=category_id,
        location_id=order.location_id,
        on=order.order_date,
        invoice_recipient_contact_id=order.invoice_recipient_contact_id,
    )
    if not answer.found or answer.source is None:
        return RateProposal(category_id, False, None, None, None, None, None, _vat_rate(db, order))
    return RateProposal(
        category_id=category_id,
        found=True,
        source=_SOURCE[answer.source],
        rate_id=answer.rate_id,
        valid_from=answer.valid_from,
        amount=answer.amount,
        entered=answer.entered,
        vat_rate=_vat_rate(db, order),
    )


@dataclass(frozen=True)
class TimeUnitInForce:
    unit: LabourTimeUnit
    work_units_per_hour: Decimal | None


def time_unit_in_force(db: Session, *, order: ServiceOrder) -> TimeUnitInForce:
    """O18/O19 at the order's location (ruling 183): hours, or work units with their number per hour."""

    unit = labour_time_unit(db, dealership_id=order.tenant_id, location_id=order.location_id)
    if unit.unit == LabourTimeUnit.WORK_UNITS.value:
        return TimeUnitInForce(LabourTimeUnit.WORK_UNITS, unit.work_units_per_hour)
    return TimeUnitInForce(LabourTimeUnit.HOURS, None)


# --- positions ---------------------------------------------------------------------------------


@dataclass
class PositionInput:
    """What the advisor typed, for a new position or a change (`*_set` says a
    field was sent at all, so «left out» and «cleared» differ)."""

    text: str | None = None
    labour_category_id: uuid.UUID | None = None
    time: Decimal | None = None
    time_unit: LabourTimeUnit | None = None
    unit_price: Decimal | None = None
    price_entered: PriceEntry | None = None
    category_set: bool = False
    time_set: bool = False
    price_set: bool = False


def get_position_or_404(db: Session, *, order: ServiceOrder, position_id: uuid.UUID) -> ServiceOrderPosition:
    position = db.scalar(
        select(ServiceOrderPosition).where(
            ServiceOrderPosition.id == position_id, ServiceOrderPosition.service_order_id == order.id
        )
    )
    if position is None:
        raise NotFoundError(f"Position {position_id} was not found.")
    return position


def _snapshot(position: ServiceOrderPosition) -> dict[str, Any]:
    return {
        "positionId": str(position.id),
        "jobId": str(position.job_id),
        "kind": position.kind.value,
        "text": position.text,
        "labourCategoryId": str(position.labour_category_id) if position.labour_category_id else None,
        "time": str(position.time_quantity) if position.time_quantity is not None else None,
        "timeUnit": position.time_unit.value if position.time_unit else None,
        "workUnitsPerHour": str(position.work_units_per_hour) if position.work_units_per_hour is not None else None,
        "unitPrice": str(position.unit_price) if position.unit_price is not None else None,
        "priceEntered": position.price_entered.value if position.price_entered else None,
        "vatRate": str(position.vat_rate) if position.vat_rate is not None else None,
        "rateSource": position.rate_source.value if position.rate_source else None,
        "labourRateId": str(position.labour_rate_id) if position.labour_rate_id else None,
    }


def _refuse_shape(kind: PositionKind, *, category_id: uuid.UUID | None, time: Decimal | None,
                  price: Decimal | None, entered: PriceEntry | None) -> None:
    """What each kind holds. A refusal names the field."""

    def refuse(field: str, message: str) -> None:
        raise UnprocessableEntityError(message, details={"reason": "service_order_position_incomplete", "field": field})

    if kind is PositionKind.TEXT:
        if category_id is not None or time is not None or price is not None:
            refuse("kind", "A text position carries no labour category, time or price.")
        return
    if category_id is None:
        refuse("labourCategoryId", "A labour position names its labour category.")
    if kind is PositionKind.LABOUR_TIME and time is None:
        refuse("time", "Labour by time needs its time.")
    if kind is PositionKind.LABOUR_FIXED and price is None:
        refuse("unitPrice", "Labour at a fixed price needs its price.")
    if price is not None and entered is None:
        refuse("priceEntered", "A typed price says whether it is net or with VAT.")


def _check_time(value: Decimal) -> None:
    if value <= 0:
        raise UnprocessableEntityError(
            "A labour time is above zero.", details={"reason": "service_order_time_not_positive"}
        )
    if _places(value) > _TIME_PLACES:
        raise UnprocessableEntityError(
            f"A labour time has at most {_TIME_PLACES} decimals.",
            details={"reason": "service_order_time_too_many_decimals"},
        )


def _check_price(value: Decimal) -> None:
    if value < 0:
        raise UnprocessableEntityError("A price is not below zero.", details={"reason": "service_order_price_negative"})
    if _places(value) > UNIT_PRICE_PLACES:
        raise UnprocessableEntityError(
            f"A price has at most {UNIT_PRICE_PLACES} decimals.",
            details={"reason": "service_order_price_too_many_decimals"},
        )


def _apply_time(db: Session, order: ServiceOrder, position: ServiceOrderPosition, data: PositionInput) -> None:
    """The time in the unit in force at the order's location, kept with that unit (check 48)."""

    if data.time is None:
        position.time_quantity = position.time_unit = position.work_units_per_hour = None
        return
    _check_time(data.time)
    in_force = time_unit_in_force(db, order=order)
    if data.time_unit is not None and data.time_unit is not in_force.unit:
        raise ConflictError(
            f"Labour time is entered in {in_force.unit.value.replace('_', ' ')} here now — check the time and enter it again.",
            details={"reason": "service_order_time_unit_changed", "timeUnit": in_force.unit.value},
        )
    position.time_quantity = data.time
    position.time_unit = in_force.unit
    position.work_units_per_hour = in_force.work_units_per_hour


def _apply_price(
    db: Session, order: ServiceOrder, position: ServiceOrderPosition, data: PositionInput, *, actor_id: uuid.UUID
) -> None:
    """The typed price leads (check 12); without one, labour by time takes the rate
    which applies here and names its source (check 11)."""

    if position.kind is PositionKind.TEXT:
        position.unit_price = position.price_entered = position.vat_rate = None
        position.rate_source = position.labour_rate_id = position.rate_valid_from = None
        return
    vat_rate = _vat_rate(db, order)
    if vat_rate is None:
        raise ConflictError(
            "No VAT rate is set for this business yet, so no price can be entered.",
            details={"reason": "service_order_vat_rate_not_set"},
        )
    if data.unit_price is not None:
        _check_price(data.unit_price)
        position.unit_price, position.price_entered = data.unit_price, data.price_entered
        position.rate_source, position.labour_rate_id, position.rate_valid_from = RateSource.TYPED, None, None
    else:
        assert position.labour_category_id is not None
        proposal = propose_rate(db, order=order, category_id=position.labour_category_id)
        if not proposal.found or proposal.amount is None:
            raise ConflictError(
                "No labour rate applies to this category at this location on the order's day — type the price.",
                details={"reason": "service_order_labour_rate_missing"},
            )
        position.unit_price, position.price_entered = proposal.amount, proposal.entered
        position.rate_source, position.labour_rate_id = proposal.source, proposal.rate_id
        position.rate_valid_from = proposal.valid_from
    position.vat_rate = vat_rate
    position.priced_at = utcnow()
    position.priced_by = actor_id


def add_position(
    db: Session,
    *,
    order: ServiceOrder,
    job: ServiceOrderJob,
    actor_id: uuid.UUID,
    kind: PositionKind,
    data: PositionInput,
) -> ServiceOrderPosition:
    refuse_frozen(order)
    _refuse_shape(kind, category_id=data.labour_category_id, time=data.time, price=data.unit_price,
                  entered=data.price_entered)
    text = (data.text or "").strip()
    if not text:
        raise UnprocessableEntityError(
            "A position needs its text.", details={"reason": "service_order_position_incomplete", "field": "text"}
        )
    last = db.scalar(select(func.max(ServiceOrderPosition.sort_index)).where(ServiceOrderPosition.job_id == job.id))
    position = ServiceOrderPosition(
        tenant_id=order.tenant_id,
        service_order_id=order.id,
        job_id=job.id,
        sort_index=(last or 0) + 1,
        kind=kind,
        text=text,
        created_by=actor_id,
        updated_by=actor_id,
    )
    if kind in _LABOUR:
        assert data.labour_category_id is not None
        position.labour_category_id = data.labour_category_id
        position.labour_category_label = _category_label(db, order, data.labour_category_id)
        position.labour_category_label_refreshed_at = utcnow()
        _apply_time(db, order, position, data)
    _apply_price(db, order, position, data, actor_id=actor_id)
    db.add(position)
    db.flush()
    record_order_change(db, order, action="service_order_position_added", actor_id=actor_id, after=_snapshot(position))
    db.commit()
    return position


def change_position(
    db: Session, *, order: ServiceOrder, position: ServiceOrderPosition, actor_id: uuid.UUID, data: PositionInput
) -> ServiceOrderPosition:
    """A change keeps what was not sent. A time sent again is read in the unit in
    force now; a time not sent keeps the unit it was typed in (check 48). The price is
    taken again only when a price, the category or the time was sent."""

    refuse_frozen(order)
    before = _snapshot(position)
    if data.text is not None:
        text = data.text.strip()
        if not text:
            raise UnprocessableEntityError(
                "A position needs its text.", details={"reason": "service_order_position_incomplete", "field": "text"}
            )
        position.text = text
    if position.kind is not PositionKind.TEXT:
        category_id = data.labour_category_id if data.category_set else position.labour_category_id
        time = data.time if data.time_set else position.time_quantity
        reprice = data.price_set or data.category_set or (data.time_set and position.kind is PositionKind.LABOUR_TIME)
        if data.price_set:
            price, entered = data.unit_price, data.price_entered
        elif position.rate_source is RateSource.TYPED:
            price, entered = position.unit_price, position.price_entered
        else:
            price, entered = None, None
        _refuse_shape(position.kind, category_id=category_id, time=time, price=price, entered=entered)
        if data.category_set and category_id != position.labour_category_id:
            assert category_id is not None
            position.labour_category_id = category_id
            position.labour_category_label = _category_label(db, order, category_id)
            position.labour_category_label_refreshed_at = utcnow()
        if data.time_set:
            _apply_time(db, order, position, PositionInput(time=data.time, time_unit=data.time_unit))
        if reprice:
            _apply_price(db, order, position, PositionInput(unit_price=price, price_entered=entered), actor_id=actor_id)
    position.updated_by = actor_id
    after = _snapshot(position)
    if after != before:
        record_order_change(
            db, order, action="service_order_position_changed", actor_id=actor_id, before=before, after=after
        )
    db.commit()
    return position


def delete_position(db: Session, *, order: ServiceOrder, position: ServiceOrderPosition, actor_id: uuid.UUID) -> None:
    """Table 3.3: gone, unless time was clocked on it (decision 64)."""

    refuse_frozen(order)
    if work_guard.time_clocked_on_position(db, tenant_id=order.tenant_id, position_id=position.id):
        raise ConflictError(
            "Time has been clocked on this position, so it cannot be deleted.",
            details={"reason": "service_order_position_time_clocked"},
        )
    before = _snapshot(position)
    db.delete(position)
    record_order_change(db, order, action="service_order_position_deleted", actor_id=actor_id, before=before)
    db.commit()
