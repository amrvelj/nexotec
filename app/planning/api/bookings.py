"""The planner page (KAN-809; slice 1, 3.8; PL1, PL2, PL4): one day of one
location, and the writes the page makes — change a booking, release it, block
resources. Work and the drop-off and pick-up slots are booked from the order
(KAN-812; Anto, 2026-11-26): the page books none.

Every route sits behind the router-level `aftersales` gate (D-A-04: 404 for
everyone without the module, before any role check), is the Betrieb's own
(tenant match; the platform tenant 404) and depends on the matrix row
`planner`: whoever has the role aftersales decides at the desk, the manager
everything (ruling 180).

Every rule is the one write path's (`services/bookings.py`): refusals are 409
with `bookingIds`, warnings 422 `planning_warnings_unconfirmed` until the
page re-sends them as `confirmedWarnings`. A change and a release take
If-Match on the booking's version; the POST honours an Idempotency-Key (the
route class), claimed only after the role check has passed.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.db import get_db
from app.planning.api.resources import guard
from app.planning.schemas.bookings import (
    BlockCreate,
    BookingMove,
    BookingOutcomeRead,
    BookingRead,
    PlannerDayRead,
)
from app.planning.services import bookings as service
from app.planning.services.booking_records import BookingOutcome, ConfirmedWarning
from app.planning.services.planner_day import planner_day

router = APIRouter(
    tags=["planner"],
    route_class=IdempotentRoute,
    dependencies=[Depends(require_entitlement(ModuleCode.AFTERSALES))],
)

_BASE = "/dealerships/{dealership_id}/planning"
_READ = require_read("planner")
_WRITE = require_write("planner")


def _outcome(outcome: BookingOutcome) -> BookingOutcomeRead:
    return BookingOutcomeRead.model_validate(outcome)


@router.get(f"{_BASE}/day", response_model=PlannerDayRead)
def get_planner_day(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID = Query(alias="locationId"),
    date: dt.date = Query(),
    principal: Principal = Depends(_READ),
    db: Session = Depends(get_db),
):
    """The location's bookings of one Europe/Zurich day and its grid (O6)."""

    guard(db, dealership_id, principal)
    return PlannerDayRead.model_validate(
        planner_day(db, dealership_id=dealership_id, location_id=location_id, date=date)
    )


@router.post(f"{_BASE}/blocks", response_model=BookingOutcomeRead, status_code=201)
def create_block(
    dealership_id: uuid.UUID,
    body: BlockCreate,
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Block people and/or workplaces (PL4). Always possible; the bookings it
    hits are marked «neu einplanen» and returned."""

    guard(db, dealership_id, principal)
    return _outcome(
        service.block(
            db,
            dealership_id=dealership_id,
            actor_id=principal.user_id,
            resource_ids=body.resource_ids,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            note=body.note,
            idempotency_key=None,
        )
    )


@router.patch(f"{_BASE}/bookings/{{booking_id}}", response_model=BookingOutcomeRead)
def move_booking(
    dealership_id: uuid.UUID,
    booking_id: uuid.UUID,
    body: BookingMove,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Change a booking's people, workplace, from and to (PL2)."""

    guard(db, dealership_id, principal)
    return _outcome(
        service.move(
            db,
            dealership_id=dealership_id,
            actor_id=principal.user_id,
            booking_id=booking_id,
            expected_version=if_match,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            resource_ids=body.resource_ids,
            confirmed_warnings=[
                ConfirmedWarning(
                    code=warning.code, resource_id=warning.resource_id, against_booking_id=warning.against_booking_id
                )
                for warning in body.confirmed_warnings
            ],
            idempotency_key=None,
        )
    )


@router.post(f"{_BASE}/bookings/{{booking_id}}/release", response_model=BookingRead)
def release_booking(
    dealership_id: uuid.UUID,
    booking_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(_WRITE),
    db: Session = Depends(get_db),
):
    """Release a booking (PL2): it holds nothing and is kept."""

    guard(db, dealership_id, principal)
    return BookingRead.model_validate(
        service.release(
            db,
            dealership_id=dealership_id,
            actor_id=principal.user_id,
            booking_id=booking_id,
            expected_version=if_match,
            idempotency_key=None,
        )
    )
