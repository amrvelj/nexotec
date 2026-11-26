"""The workshop order as the screens read it (KAN-810): the order with its
jobs, positions and two-part total, a row of the list, the rate proposal,
and the history — the platform audit trail of the order (exit criterion 6).
Reads only."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJob, ServiceOrderPosition
from app.aftersales.schemas.service_order import (
    CancellationRead,
    HistoryEntryRead,
    HistoryRead,
    JobRead,
    LabourRateProposalRead,
    LabourTimeRead,
    OrderTotalsRead,
    PartyRead,
    PayerTotalRead,
    PositionRead,
    ServiceOrderRead,
    ServiceOrderSummary,
    UnitPriceRead,
    VatLineRead,
)
from app.aftersales.services import order_money
from app.aftersales.services.positions import RateProposal, TimeUnitInForce
from app.aftersales.services.service_order import ENTITY_TYPE, waiting_for_answer_ids
from app.core.audit import list_audit_events
from app.core.money import PayerTotal, Price
from app.platform.public import get_dealership_cash_rounding, get_group_display_labels


def _unit_price(price: Price | None) -> UnitPriceRead | None:
    figures = order_money.unit_price_figures(price)
    if price is None or figures is None:
        return None
    return UnitPriceRead(amount=price.amount, entered=price.entered, vat_rate=price.vat_rate, net=figures[0],
                         gross=figures[1])


def _position(position: ServiceOrderPosition, payer: str) -> PositionRead:
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
        unit_price=_unit_price(order_money.position_price(position)),
        line_net=line.net if line else None,
        line_gross=line.gross if line else None,
        rate_source=position.rate_source,
        labour_rate_id=position.labour_rate_id,
        rate_valid_from=position.rate_valid_from,
        priced_at=position.priced_at,
        priced_by=position.priced_by,
    )


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


def summary(order: ServiceOrder, *, waiting: bool) -> ServiceOrderSummary:
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
    )


def summaries(db: Session, orders: list[ServiceOrder]) -> list[ServiceOrderSummary]:
    waiting = waiting_for_answer_ids(db, [order.id for order in orders])
    return [summary(order, waiting=order.id in waiting) for order in orders]


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
    base = summary(order, waiting=any(job.answer_state.value == "waiting" for job in jobs))
    return ServiceOrderRead(
        **base.model_dump(),
        invoice_recipient=PartyRead(contact_id=order.invoice_recipient_contact_id, label=order.invoice_recipient_label),
        driver=(
            PartyRead(contact_id=order.driver_contact_id, label=order.driver_label or "")
            if order.driver_contact_id
            else None
        ),
        jobs=[
            JobRead(
                id=job.id,
                sort_index=job.sort_index,
                name=job.name,
                billing_category=job.billing_category,
                answer_state=job.answer_state,
                answered_at=job.answered_at,
                answered_by=job.answered_by,
                positions=[_position(p, payer_of[job.id]) for p in positions if p.job_id == job.id],
            )
            for job in jobs
        ],
        totals=OrderTotalsRead(
            accepted=[_payer_total(t) for t in totals.accepted], open=[_payer_total(t) for t in totals.open]
        ),
        cancellation=cancellation,
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
