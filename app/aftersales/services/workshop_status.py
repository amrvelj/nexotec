"""The workshop status of an order (KAN-813 criterion 5; build slice 1,
section 3.9; ADR-213).

planned, car here, in work, work done, checked, ready for pick-up, handed
over — **worked out, never set**: from the stay (here, left), the clocks
(KAN-814), the positions (done, KAN-814), the check (KAN-815) and the
commercial state of the order. `work_out` is the one rule; `refresh` writes
its answer into the projection `service_order_workshop_status` that the
order list reads, and is the projection's only writer
(`test_kan813_stay.py::test_no_write_path_sets_the_workshop_status`).

`refresh` runs inside every write that changes an input: every change of
the order goes through `service_order.record_order_change`, which calls it,
and the creation of an order and the arrival of its car call it too.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    JobAnswerState,
    PositionKind,
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderPosition,
    ServiceOrderState,
)
from app.aftersales.models.service_stay import (
    ServiceOrderStay,
    ServiceOrderWorkshopStatus,
    ServiceStay,
    WorkshopStatus,
)
from app.aftersales.services import work_guard
from app.core.base import utcnow

_LABOUR = frozenset({PositionKind.LABOUR_FIXED, PositionKind.LABOUR_TIME})
# Table 3.3: «done» comes with KAN-814; until then no position is done.
_DONE = "done"


@dataclasses.dataclass(frozen=True)
class StatusInputs:
    order_state: ServiceOrderState
    car_here: bool
    car_left: bool
    clock_running: bool
    labour_count: int
    labour_done: int
    check_needed: bool
    check_passed: bool


def work_out(inputs: StatusInputs) -> WorkshopStatus:
    """Section 3.9. A car that left is handed over; a car not here is planned; then the
    work: a clock or done labour is «in work», all labour of the accepted jobs done is
    «work done», checked when no position needs the check (decision 50) or the check
    passed, ready for pick-up once the order is ready for billing."""

    if not inputs.car_here:
        return WorkshopStatus.HANDED_OVER if inputs.car_left else WorkshopStatus.PLANNED
    if inputs.clock_running:
        return WorkshopStatus.IN_WORK
    if inputs.labour_count and inputs.labour_done == inputs.labour_count:
        if inputs.check_needed and not inputs.check_passed:
            return WorkshopStatus.WORK_DONE
        if inputs.order_state is ServiceOrderState.READY_FOR_BILLING:
            return WorkshopStatus.READY_FOR_PICKUP
        return WorkshopStatus.CHECKED
    if inputs.labour_done:
        return WorkshopStatus.IN_WORK
    return WorkshopStatus.CAR_HERE


def stays_of(db: Session, order_id: uuid.UUID) -> list[ServiceStay]:
    """The order's stays, latest arrival last."""

    return list(
        db.scalars(
            select(ServiceStay)
            .join(ServiceOrderStay, ServiceOrderStay.stay_id == ServiceStay.id)
            .where(ServiceOrderStay.service_order_id == order_id)
            .order_by(ServiceStay.arrived_at, ServiceStay.id)
        )
    )


def inputs_for(db: Session, order: ServiceOrder) -> StatusInputs:
    stays = stays_of(db, order.id)
    accepted_jobs = set(
        db.scalars(
            select(ServiceOrderJob.id).where(
                ServiceOrderJob.service_order_id == order.id, ServiceOrderJob.answer_state == JobAnswerState.ACCEPTED
            )
        )
    )
    labour = [
        p
        for p in db.scalars(select(ServiceOrderPosition).where(ServiceOrderPosition.service_order_id == order.id))
        if p.kind in _LABOUR and p.job_id in accepted_jobs
    ]
    return StatusInputs(
        order_state=order.state,
        car_here=any(stay.departed_at is None for stay in stays),
        car_left=bool(stays) and all(stay.departed_at is not None for stay in stays),
        clock_running=work_guard.clock_running_on_order(db, tenant_id=order.tenant_id, order_id=order.id),
        labour_count=len(labour),
        labour_done=sum(1 for p in labour if p.state.value == _DONE),
        check_needed=any(p.check_needed for p in labour),
        # The quality check is KAN-815's; until then no check has passed.
        check_passed=False,
    )


def refresh(db: Session, order: ServiceOrder) -> WorkshopStatus:
    """Works the status out and writes it into the projection. Inside the caller's
    transaction; flushes, does not commit."""

    db.flush()
    status = work_out(inputs_for(db, order))
    row = db.scalar(
        select(ServiceOrderWorkshopStatus).where(ServiceOrderWorkshopStatus.service_order_id == order.id)
    )
    if row is None:
        db.add(
            ServiceOrderWorkshopStatus(
                tenant_id=order.tenant_id, service_order_id=order.id, status=status, refreshed_at=utcnow()
            )
        )
    elif row.status is not status:
        row.status, row.refreshed_at = status, utcnow()
    return status


def statuses_of(db: Session, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, WorkshopStatus]:
    """The projection, for the list (criterion 5)."""

    if not order_ids:
        return {}
    return {
        row.service_order_id: row.status
        for row in db.scalars(
            select(ServiceOrderWorkshopStatus).where(ServiceOrderWorkshopStatus.service_order_id.in_(order_ids))
        )
    }
