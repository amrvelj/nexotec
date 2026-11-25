"""The planner's rules (KAN-808; ADR-221 (2); slice 1, section 3.8): one pure
read that every booking entry point calls, under the Betrieb's booking lock
(`locks.lock_bookings`), before it writes.

`judge` answers a `Verdict`: what refuses the write and what warns.

Refused (409, with the bookings in the way):

- `planning_car_at_another_workplace` (check 16): the same car on work at
  another workplace at an overlapping time.
- `planning_workplace_taken` (check 16): another car on work at the same
  workplace at an overlapping time. The same car twice at one workplace —
  two jobs on one car — is allowed.
- `planning_person_twice_on_job` (check 17): the same person on the same job
  twice at an overlapping time. Two people on one job, and one person on two
  different jobs at one time, are allowed (Anto, 2026-11-25).

Warned and bookable (Anto, 2026-11-25):

- `outside_working_hours` (check 18), per person: a piece of the booking,
  split per local day (Europe/Zurich), that lies outside that weekday's
  working hours (both intervals, a lunch break between them). A person with
  no working hours entered gets no warning; one with hours, but none on that
  weekday, is outside them.
- `before_drop_off`: work of reference R starting before R's drop-off slot
  ends; `after_pick_up`: work of R ending after R's pick-up slot starts.
- `resource_blocked`: a resource held by an active block at an overlapping
  time.

Checks 16 and 17 are between work bookings; the car is checked within the
Betrieb only (Anto, 2026-11-25). Two intervals overlap when one starts
before the other ends and ends after it starts, so bookings that only touch
do not. Released bookings hold nothing; the booking being moved is not
judged against itself. A block is judged by nothing: a block is always
possible, and the bookings it hits are marked «neu einplanen»
(`bookings_hit_by_block`). Working hours are local wall-clock times; the
instants are UTC (`app.core.relative_dates.session_zone`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.relative_dates import session_zone
from app.planning.models.booking import Booking, BookingKind, BookingResource, BookingStatus, WarningCode
from app.planning.models.resource import Resource, ResourceKind, WorkingHours

CAR_AT_ANOTHER_WORKPLACE = "planning_car_at_another_workplace"
WORKPLACE_TAKEN = "planning_workplace_taken"
PERSON_TWICE_ON_JOB = "planning_person_twice_on_job"


@dataclass(frozen=True)
class ResourceFacts:
    id: uuid.UUID
    kind: ResourceKind


@dataclass(frozen=True)
class Candidate:
    """The booking as it would stand after the write."""

    dealership_id: uuid.UUID
    kind: BookingKind
    starts_at: dt.datetime
    ends_at: dt.datetime
    resources: tuple[ResourceFacts, ...]
    vehicle_id: uuid.UUID | None = None
    reference_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    # The booking being moved: never judged against itself.
    replaces_booking_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Refusal:
    reason: str
    booking_ids: tuple[uuid.UUID, ...]
    resource_id: uuid.UUID | None = None


@dataclass(frozen=True)
class BookingWarning:
    code: WarningCode
    resource_id: uuid.UUID | None = None
    against_booking_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Verdict:
    refusals: tuple[Refusal, ...]
    warnings: tuple[BookingWarning, ...]

    @property
    def warning_codes(self) -> frozenset[WarningCode]:
        return frozenset(warning.code for warning in self.warnings)


# --- reads ----------------------------------------------------------------------------------


def _overlapping(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    kinds: Collection[BookingKind],
    exclude: uuid.UUID | None,
) -> list[Booking]:
    stmt = select(Booking).where(
        Booking.tenant_id == dealership_id,
        Booking.status == BookingStatus.BOOKED,
        Booking.kind.in_(list(kinds)),
        Booking.starts_at < ends_at,
        Booking.ends_at > starts_at,
    )
    if exclude is not None:
        stmt = stmt.where(Booking.id != exclude)
    return list(db.scalars(stmt.order_by(Booking.starts_at, Booking.id).execution_options(populate_existing=True)))


def resources_of(db: Session, booking_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[ResourceFacts]]:
    """Each booking's resources, with their kind, in the order they were written."""

    ids = set(booking_ids)
    found: dict[uuid.UUID, list[ResourceFacts]] = {booking_id: [] for booking_id in ids}
    if not ids:
        return found
    rows = db.execute(
        select(BookingResource.booking_id, Resource.id, Resource.kind)
        .join(Resource, Resource.id == BookingResource.resource_id)
        .where(BookingResource.booking_id.in_(ids))
        .order_by(BookingResource.id)
    )
    for booking_id, resource_id, kind in rows:
        found[booking_id].append(ResourceFacts(id=resource_id, kind=kind))
    return found


def _workplace(resources: Sequence[ResourceFacts]) -> uuid.UUID | None:
    return next((resource.id for resource in resources if resource.kind is ResourceKind.WORKPLACE), None)


# --- the refusals ---------------------------------------------------------------------------


def _refusals(db: Session, candidate: Candidate) -> list[Refusal]:
    if candidate.kind is not BookingKind.WORK:
        return []
    others = _overlapping(
        db,
        dealership_id=candidate.dealership_id,
        starts_at=candidate.starts_at,
        ends_at=candidate.ends_at,
        kinds=(BookingKind.WORK,),
        exclude=candidate.replaces_booking_id,
    )
    held = resources_of(db, (other.id for other in others))
    workplace = _workplace(candidate.resources)
    people = {resource.id for resource in candidate.resources if resource.kind is ResourceKind.PERSON}

    car_elsewhere: list[uuid.UUID] = []
    workplace_taken: list[uuid.UUID] = []
    twice: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for other in others:
        other_workplace = _workplace(held[other.id])
        if other.vehicle_id == candidate.vehicle_id and other_workplace != workplace:
            car_elsewhere.append(other.id)
        if other_workplace == workplace and other.vehicle_id != candidate.vehicle_id:
            workplace_taken.append(other.id)
        if other.job_id == candidate.job_id:
            for resource in held[other.id]:
                if resource.id in people:
                    twice[resource.id].append(other.id)

    refusals: list[Refusal] = []
    if car_elsewhere:
        refusals.append(Refusal(reason=CAR_AT_ANOTHER_WORKPLACE, booking_ids=tuple(car_elsewhere)))
    if workplace_taken:
        refusals.append(Refusal(reason=WORKPLACE_TAKEN, booking_ids=tuple(workplace_taken), resource_id=workplace))
    for person in sorted(twice):
        refusals.append(Refusal(reason=PERSON_TWICE_ON_JOB, booking_ids=tuple(twice[person]), resource_id=person))
    return refusals


# --- the warnings ---------------------------------------------------------------------------


def _local_day_pieces(starts_at: dt.datetime, ends_at: dt.datetime) -> list[tuple[dt.date, dt.datetime, dt.datetime]]:
    """`[starts_at, ends_at)` cut at each local midnight: (local date, start, end)
    per day it touches, the instants in UTC. A day of 23 or 25 hours (a
    daylight-saving change) is cut at its real midnights."""

    zone = session_zone()
    pieces: list[tuple[dt.date, dt.datetime, dt.datetime]] = []
    day = starts_at.astimezone(zone).date()
    while True:
        day_start = dt.datetime.combine(day, dt.time(0), tzinfo=zone).astimezone(dt.UTC)
        next_start = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=zone).astimezone(dt.UTC)
        piece_start, piece_end = max(starts_at, day_start), min(ends_at, next_start)
        if piece_start < piece_end:
            pieces.append((day, piece_start, piece_end))
        if next_start >= ends_at:
            return pieces
        day += dt.timedelta(days=1)


def _intervals(day: dt.date, hours: WorkingHours) -> list[tuple[dt.datetime, dt.datetime]]:
    zone = session_zone()

    def instant(at: dt.time) -> dt.datetime:
        return dt.datetime.combine(day, at, tzinfo=zone).astimezone(dt.UTC)

    found = [(instant(hours.starts_at), instant(hours.ends_at))]
    if hours.second_starts_at is not None and hours.second_ends_at is not None:
        found.append((instant(hours.second_starts_at), instant(hours.second_ends_at)))
    return found


def outside_working_hours(
    week: Sequence[WorkingHours], starts_at: dt.datetime, ends_at: dt.datetime
) -> bool:
    """Whether any piece of the interval lies outside the person's working
    hours. An empty week — no hours entered — is never outside (rule 7:
    «not set» is quiet)."""

    if not week:
        return False
    by_weekday = {row.weekday: row for row in week}
    for day, piece_start, piece_end in _local_day_pieces(starts_at, ends_at):
        hours = by_weekday.get(day.isoweekday())
        if hours is None:
            return True
        if not any(start <= piece_start and piece_end <= end for start, end in _intervals(day, hours)):
            return True
    return False


def _working_hours_of(db: Session, person_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, list[WorkingHours]]:
    found: dict[uuid.UUID, list[WorkingHours]] = {person_id: [] for person_id in person_ids}
    if not person_ids:
        return found
    for row in db.scalars(select(WorkingHours).where(WorkingHours.resource_id.in_(set(person_ids)))):
        found[row.resource_id].append(row)
    return found


def slots_of(
    db: Session, *, dealership_id: uuid.UUID, reference_id: uuid.UUID, exclude: uuid.UUID | None = None
) -> list[Booking]:
    """Reference R's booked drop-off and pick-up slots."""

    stmt = select(Booking).where(
        Booking.tenant_id == dealership_id,
        Booking.reference_id == reference_id,
        Booking.status == BookingStatus.BOOKED,
        Booking.kind.in_([BookingKind.DROP_OFF, BookingKind.PICK_UP]),
    )
    if exclude is not None:
        stmt = stmt.where(Booking.id != exclude)
    return list(db.scalars(stmt.order_by(Booking.starts_at, Booking.id).execution_options(populate_existing=True)))


def slot_warnings(starts_at: dt.datetime, ends_at: dt.datetime, slots: Iterable[Booking]) -> list[BookingWarning]:
    """Work at `[starts_at, ends_at)` against R's slots: before the drop-off
    (it starts before a drop-off slot ends), after the pick-up (it ends after
    a pick-up slot starts)."""

    found: list[BookingWarning] = []
    for slot in slots:
        if slot.kind is BookingKind.DROP_OFF and starts_at < slot.ends_at:
            found.append(BookingWarning(code=WarningCode.BEFORE_DROP_OFF, against_booking_id=slot.id))
        if slot.kind is BookingKind.PICK_UP and ends_at > slot.starts_at:
            found.append(BookingWarning(code=WarningCode.AFTER_PICK_UP, against_booking_id=slot.id))
    return found


def _warnings(db: Session, candidate: Candidate) -> list[BookingWarning]:
    if candidate.kind is BookingKind.BLOCK:
        return []
    warnings: list[BookingWarning] = []
    people = [resource.id for resource in candidate.resources if resource.kind is ResourceKind.PERSON]
    weeks = _working_hours_of(db, people)
    for person in people:
        if outside_working_hours(weeks[person], candidate.starts_at, candidate.ends_at):
            warnings.append(BookingWarning(code=WarningCode.OUTSIDE_WORKING_HOURS, resource_id=person))

    if candidate.kind is BookingKind.WORK and candidate.reference_id is not None:
        slots = slots_of(db, dealership_id=candidate.dealership_id, reference_id=candidate.reference_id)
        warnings += slot_warnings(candidate.starts_at, candidate.ends_at, slots)

    blocks = _overlapping(
        db,
        dealership_id=candidate.dealership_id,
        starts_at=candidate.starts_at,
        ends_at=candidate.ends_at,
        kinds=(BookingKind.BLOCK,),
        exclude=candidate.replaces_booking_id,
    )
    held = resources_of(db, (block.id for block in blocks))
    mine = [resource.id for resource in candidate.resources]
    for block in blocks:
        blocked = {resource.id for resource in held[block.id]}
        for resource_id in mine:
            if resource_id in blocked:
                warnings.append(
                    BookingWarning(code=WarningCode.RESOURCE_BLOCKED, resource_id=resource_id, against_booking_id=block.id)
                )
    return warnings


def judge(db: Session, candidate: Candidate) -> Verdict:
    """What refuses the candidate and what warns. Reads only."""

    return Verdict(refusals=tuple(_refusals(db, candidate)), warnings=tuple(_warnings(db, candidate)))


# --- what a write leaves in the wrong place -------------------------------------------------


def bookings_hit_by_block(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    resource_ids: Collection[uuid.UUID],
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    block_id: uuid.UUID | None,
) -> list[Booking]:
    """The booked non-block bookings holding one of the block's resources at
    an overlapping time."""

    others = _overlapping(
        db,
        dealership_id=dealership_id,
        starts_at=starts_at,
        ends_at=ends_at,
        kinds=(BookingKind.WORK, BookingKind.DROP_OFF, BookingKind.PICK_UP),
        exclude=block_id,
    )
    held = resources_of(db, (other.id for other in others))
    wanted = set(resource_ids)
    return [other for other in others if any(resource.id in wanted for resource in held[other.id])]


def work_outside_slots(db: Session, *, dealership_id: uuid.UUID, reference_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """Reference R's booked work that starts before a drop-off slot ends or
    ends after a pick-up slot starts: work id -> the first slot it is outside."""

    slots = slots_of(db, dealership_id=dealership_id, reference_id=reference_id)
    if not slots:
        return {}
    work = db.scalars(
        select(Booking)
        .where(
            Booking.tenant_id == dealership_id,
            Booking.reference_id == reference_id,
            Booking.status == BookingStatus.BOOKED,
            Booking.kind == BookingKind.WORK,
        )
        .order_by(Booking.starts_at, Booking.id)
        .execution_options(populate_existing=True)
    )
    outside: dict[uuid.UUID, uuid.UUID] = {}
    for booking in work:
        found = slot_warnings(booking.starts_at, booking.ends_at, slots)
        if found and found[0].against_booking_id is not None:
            outside[booking.id] = found[0].against_booking_id
    return outside
