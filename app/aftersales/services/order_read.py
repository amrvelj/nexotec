"""The workshop order as the screens read it (KAN-810): the order with its
jobs, positions and two-part total, a row of the list, the rate proposal,
and the history — the platform audit trail of the order (exit criterion 6).
Reads only."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJob, ServiceOrderPosition
from app.aftersales.models.service_stay import ServiceOrderStay, ServiceStay, WorkshopStatus
from app.aftersales.schemas.service_order import (
    AmountRead,
    CancellationRead,
    HistoryEntryRead,
    HistoryRead,
    JobAnswerRead,
    JobRead,
    LabourRateProposalRead,
    LabourTimeRead,
    OrderTotalsRead,
    PartyRead,
    PayerTotalRead,
    PositionRead,
    ServiceOrderRead,
    ServiceOrderSummary,
    StayOrderRead,
    StayRead,
    UnitPriceRead,
    VatLineRead,
)
from app.aftersales.services import answers as answer_service
from app.aftersales.services import order_money, work_guard, workshop_status
from app.aftersales.services.positions import RateProposal, TimeUnitInForce
from app.aftersales.services.service_order import ENTITY_TYPE, waiting_for_answer_ids
from app.core.audit import list_audit_events
from app.core.money import PayerTotal, Price
from app.platform.public import get_dealership_cash_rounding, get_group_display_labels
from app.vehicle.public import current_mileage


def _unit_price(price: Price | None) -> UnitPriceRead | None:
    figures = order_money.unit_price_figures(price)
    if price is None or figures is None:
        return None
    return UnitPriceRead(amount=price.amount, entered=price.entered, vat_rate=price.vat_rate, net=figures[0],
                         gross=figures[1])


def _position(position: ServiceOrderPosition, payer: str, users: dict | None = None) -> PositionRead:
    hours = order_money.position_hours(position)
    time = None
    if position.time_quantity is not None and position.time_unit is not None:
        shown = order_money.shown_hours(hours)
        assert shown is not None
        time = LabourTimeRead(
            quantity=position.time_quantity, unit=position.time_unit,
            work_units_per_hour=position.work_units_per_hour, hours=shown,
        )
    line = order_money.line_figures(order_money.position_line(position, payer=payer))
    return PositionRead(
        id=position.id,
        job_id=position.job_id,
        sort_index=position.sort_index,
        kind=position.kind,
        state=position.state,
        text=position.text,
        labour_category_id=position.labour_category_id,
        labour_category_label=position.labour_category_label,
        time=time,
        check_needed=position.check_needed,
        unit_price=_unit_price(order_money.position_price(position)),
        line_net=line.net if line else None,
        line_gross=line.gross if line else None,
        rate_source=position.rate_source,
        labour_rate_id=position.labour_rate_id,
        rate_valid_from=position.rate_valid_from,
        priced_at=position.priced_at,
        priced_by=position.priced_by,
        priced_by_label=(users or {}).get(position.priced_by) if position.priced_by else None,
    )


def _amount(net, gross) -> AmountRead | None:
    return AmountRead(net=net, gross=gross) if net is not None and gross is not None else None


def _payer_total(total: PayerTotal) -> PayerTotalRead:
    return PayerTotalRead(
        payer=total.payer,  # type: ignore[arg-type]  # the billing category's value
        net=total.net,
        vat_lines=[VatLineRead(rate=v.rate, net=v.net, vat=v.vat) for v in total.vat_lines],
        vat=total.vat,
        total=total.total,
        rounding=total.rounding,
        rounded_total=total.rounded_total,
    )


def _user_labels(db: Session, *, order: ServiceOrder, group_id: uuid.UUID, user_ids: set[uuid.UUID]) -> dict:
    if not user_ids:
        return {}
    _, users = get_group_display_labels(
        db, group_id=group_id, dealership_id=order.tenant_id, dealership_ids=set(), user_ids=user_ids
    )
    return users


def summary(order: ServiceOrder, *, waiting: bool, status: WorkshopStatus | None = None) -> ServiceOrderSummary:
    return ServiceOrderSummary(
        id=order.id,
        version=order.version,
        order_number=order.order_number,
        state=order.state,
        order_date=order.order_date,
        location_id=order.location_id,
        location_label=order.location_label,
        contact=PartyRead(contact_id=order.contact_id, label=order.contact_label),
        vehicle_id=order.vehicle_id,
        vehicle_label=order.vehicle_label,
        waiting_for_answer=waiting,
        created_at=order.created_at,
        workshop_status=status or WorkshopStatus.PLANNED,
    )


def summaries(db: Session, orders: list[ServiceOrder]) -> list[ServiceOrderSummary]:
    ids = [order.id for order in orders]
    waiting = waiting_for_answer_ids(db, ids)
    statuses = workshop_status.statuses_of(db, ids)
    return [summary(order, waiting=order.id in waiting, status=statuses.get(order.id)) for order in orders]


def _stay_read(db: Session, *, order: ServiceOrder, group_id: uuid.UUID) -> StayRead | None:
    stays = workshop_status.stays_of(db, order.id)
    running = [stay for stay in stays if stay.departed_at is None]
    stay: ServiceStay | None = running[-1] if running else (stays[-1] if stays else None)
    if stay is None:
        return None
    labels = _user_labels(db, order=order, group_id=group_id, user_ids={stay.arrived_by} if stay.arrived_by else set())
    held = db.execute(
        select(ServiceOrder.id, ServiceOrder.order_number)
        .join(ServiceOrderStay, ServiceOrderStay.service_order_id == ServiceOrder.id)
        .where(ServiceOrderStay.stay_id == stay.id)
        .order_by(ServiceOrder.order_number)
    ).all()
    return StayRead(
        id=stay.id,
        arrived_at=stay.arrived_at,
        arrived_by=stay.arrived_by,
        arrived_by_label=labels.get(stay.arrived_by) if stay.arrived_by else None,
        arrival_channel=stay.arrival_channel,
        arrival_mileage=stay.arrival_mileage,
        previous_mileage=stay.previous_mileage,
        mileage_below_previous=stay.previous_mileage is not None and stay.arrival_mileage < stay.previous_mileage,
        key_tag=stay.key_tag,
        parking_place=stay.parking_place,
        remark=stay.arrival_remark,
        departed_at=stay.departed_at,
        orders=[StayOrderRead(id=row.id, order_number=row.order_number) for row in held],
    )


def order_read(db: Session, *, order: ServiceOrder, group_id: uuid.UUID) -> ServiceOrderRead:
    jobs = list(
        db.scalars(
            select(ServiceOrderJob)
            .where(ServiceOrderJob.service_order_id == order.id)
            .order_by(ServiceOrderJob.sort_index, ServiceOrderJob.id)
        )
    )
    positions = list(
        db.scalars(
            select(ServiceOrderPosition)
            .where(ServiceOrderPosition.service_order_id == order.id)
            .order_by(ServiceOrderPosition.sort_index, ServiceOrderPosition.id)
        )
    )
    payer_of = {job.id: job.billing_category.value for job in jobs}
    totals = order_money.order_totals(jobs, positions, cash_rounding=get_dealership_cash_rounding(db, order.tenant_id))
    cancellation = None
    if order.cancelled_at is not None:
        labels = _user_labels(db, order=order, group_id=group_id,
                              user_ids={order.cancelled_by} if order.cancelled_by else set())
        cancellation = CancellationRead(
            cancelled_at=order.cancelled_at,
            cancelled_by=order.cancelled_by,
            cancelled_by_label=labels.get(order.cancelled_by) if order.cancelled_by else None,
            reason=order.cancel_reason or "",
        )
    answers = answer_service.answers_of(db, order_id=order.id)
    users = _user_labels(
        db, order=order, group_id=group_id,
        user_ids={p.priced_by for p in positions if p.priced_by} | {a.recorded_by for a in answers if a.recorded_by},
    )
    without_answer = answer_service.work_without_answer(db, order=order, jobs=jobs)

    def job_read(job: ServiceOrderJob) -> JobRead:
        amount = order_money.job_amount(job, positions)
        before = answer_service.accepted_before(job, answers)
        return JobRead(
            id=job.id,
            sort_index=job.sort_index,
            name=job.name,
            billing_category=job.billing_category,
            answer_state=job.answer_state,
            answered_at=job.answered_at,
            answered_by=job.answered_by,
            positions=[_position(p, payer_of[job.id], users) for p in positions if p.job_id == job.id],
            amount=_amount(amount.net, amount.gross) if amount else None,
            accepted_before=_amount(before.amount_net, before.amount_gross) if before else None,
            answers=[
                JobAnswerRead(
                    id=a.id, result=a.result, channel=a.channel, note=a.note,
                    amount=_amount(a.amount_net, a.amount_gross), hours_billed=a.hours_billed,
                    recorded_at=a.recorded_at, recorded_by=a.recorded_by,
                    recorded_by_label=users.get(a.recorded_by) if a.recorded_by else None,
                )
                for a in answers
                if a.job_id == job.id
            ],
            stopped=job.stopped_at is not None,
            hours_billed=job.hours_billed,
            time_clocked=work_guard.time_clocked_on_job(db, tenant_id=order.tenant_id, job_id=job.id),
            work_without_answer=job.id in without_answer,
        )

    base = summary(
        order, waiting=any(job.answer_state.value == "waiting" for job in jobs),
        status=workshop_status.statuses_of(db, [order.id]).get(order.id),
    )
    return ServiceOrderRead(
        **base.model_dump(),
        invoice_recipient=PartyRead(contact_id=order.invoice_recipient_contact_id, label=order.invoice_recipient_label),
        driver=(
            PartyRead(contact_id=order.driver_contact_id, label=order.driver_label or "")
            if order.driver_contact_id
            else None
        ),
        jobs=[job_read(job) for job in jobs],
        totals=OrderTotalsRead(
            accepted=[_payer_total(t) for t in totals.accepted], open=[_payer_total(t) for t in totals.open]
        ),
        cancellation=cancellation,
        work_without_answer=bool(without_answer),
        stay=_stay_read(db, order=order, group_id=group_id),
        vehicle_mileage=current_mileage(db, order.vehicle_id),
        updated_at=order.updated_at,
    )


def proposal_read(proposal: RateProposal, unit: TimeUnitInForce) -> LabourRateProposalRead:
    price = (
        Price(amount=proposal.amount, entered=proposal.entered, vat_rate=proposal.vat_rate)
        if proposal.found and proposal.amount is not None and proposal.entered is not None
        and proposal.vat_rate is not None
        else None
    )
    return LabourRateProposalRead(
        labour_category_id=proposal.category_id,
        found=proposal.found,
        source=proposal.source,
        labour_rate_id=proposal.rate_id,
        valid_from=proposal.valid_from,
        unit_price=_unit_price(price),
        time_unit=unit.unit,
        work_units_per_hour=unit.work_units_per_hour,
    )


def history(db: Session, *, order: ServiceOrder, group_id: uuid.UUID) -> HistoryRead:
    events = list_audit_events(db, entity_type=ENTITY_TYPE, entity_id=order.id, tenant_id=order.tenant_id)
    labels = _user_labels(db, order=order, group_id=group_id,
                          user_ids={e.actor_id for e in events if e.actor_id is not None})
    return HistoryRead(
        items=[
            HistoryEntryRead(
                id=e.id,
                action=e.action,
                actor_id=e.actor_id,
                actor_label=labels.get(e.actor_id) if e.actor_id else None,
                before=e.before,
                after=e.after,
                reason=e.reason,
                created_at=e.created_at,
            )
            for e in events
        ]
    )
