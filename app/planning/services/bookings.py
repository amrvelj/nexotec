"""The one write path of the planner's bookings (KAN-808 PR-1c; ADR-221):
book, move, release, block, and «the bookings of reference R».

Every write, in this order:

1. takes the Betrieb's booking lock (ADR-221 (1)) — before anything else, so
   a replayed key and two writers racing on one key are judged one after the
   other;
2. replays its idempotency key, if it was seen (the booking as it now
   stands);
3. loads the resources inside the Betrieb (another Betrieb's or none: 404);
4. checks the booking's shape (422): a work booking holds exactly one
   workplace and zero or more people, for a job on a car of a reference; a
   drop-off or pick-up slot one or more resources for a reference; a block one
   or more resources and an optional note of at most 200 characters (Anto,
   2026-11-25). The resources are active and at one location, and that
   location is active;
5. reads O6 and O7 at that location (`planner_settings`): work and slots start
   and end on the grid, a slot is O7 long; a block reads neither;
6. judges (`booking_rules.judge`) — not a block: a block is always possible;
7. raises the first refusal (409, with the bookings in the way);
8. refuses a warning not confirmed (422 `planning_warnings_unconfirmed`,
   listing each), until the caller re-sends naming every code (T2) — a
   warning that appears between the two calls is never overruled silently;
9. writes the booking, its resources, each overruled warning twice (a row on
   the booking and an audit row; ADR-221 (6)) and the booking's audit row;
10. marks what the write left in the wrong place «neu einplanen»: the
    bookings a block (booked or moved) newly hits (cause `blocked`), the work
    of R that a booked or moved slot newly leaves outside it (cause
    `outside_drop_off_pick_up`). What was already in the wrong place before
    the write — booked knowingly against a warning — is not marked again.
    Each marked booking gets `version + 1`, an audit row and its own event;
11. publishes the events (`booking_facts`);
12. stores the idempotency record and commits — the commit home of
    `planning.public`'s writes (ADR-047 pattern B).

A refused write rolls its own transaction back before the refusal reaches
the caller, so the Betrieb's booking lock is never held past the call.

A move takes the booking's version (a stale one is the core 409), refuses a
released booking (409 `planning_booking_released`), and clears the
booking's «neu einplanen» mark. Without a new end, a slot is O7 long again
and work or a block keeps its length. A release frees what the booking
holds; nothing is unmarked by it.

`mark_bookings_of_deactivated_resource` is the one write here that does not
commit: a resource's deactivation (`resources.change_resource`) calls it in
its own transaction, under the same booking lock.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.idempotency import find_cached_response, store_response
from app.core.relative_dates import session_zone
from app.planning.models.booking import (
    NOTE_LENGTH,
    Booking,
    BookingChange,
    BookingKind,
    BookingResource,
    BookingStatus,
    OverruledWarning,
    ReplanCause,
    WarningCode,
)
from app.planning.models.resource import LABEL_LENGTH, Resource, ResourceKind
from app.planning.services import booking_rules as rules
from app.planning.services.booking_facts import (
    BOOKING_BOOKED_EVENT,
    BOOKING_MARKED_FOR_REPLANNING_EVENT,
    BOOKING_MOVED_EVENT,
    BOOKING_RELEASED_EVENT,
    publish_booking_fact,
)
from app.planning.services.booking_records import (
    BookingOutcome,
    BookingRecord,
    LabelledId,
    Reference,
    bookings_by_id,
    record_of,
    records_of,
)
from app.planning.services.locks import lock_bookings
from app.planning.services.planner_settings import grid_minutes, slot_minutes
from app.planning.services.platform_refs import actor_name_for, require_active_location

ENTITY_TYPE = "planning_booking"
SLOTS = (BookingKind.DROP_OFF, BookingKind.PICK_UP)
_REFERENCE_TYPE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")

_REFUSAL_MESSAGES = {
    rules.CAR_AT_ANOTHER_WORKPLACE: "The car is at another workplace at that time.",
    rules.WORKPLACE_TAKEN: "Another car is at this workplace at that time.",
    rules.PERSON_TWICE_ON_JOB: "This person is already on this job at that time.",
}


# --- refusals -------------------------------------------------------------------------------


def _refuse(reason: str, message: str, **details: Any) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, **details})


def _instant(value: dt.datetime, field: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _refuse("planning_interval_invalid", "A booking's times are instants with a time zone.", field=field)
    return value.astimezone(dt.UTC)


def _raise_refusal(verdict: rules.Verdict) -> None:
    if not verdict.refusals:
        return
    first = verdict.refusals[0]
    details: dict[str, Any] = {"reason": first.reason, "bookingIds": [str(i) for i in first.booking_ids]}
    if first.resource_id is not None:
        details["resourceId"] = str(first.resource_id)
    raise ConflictError(_REFUSAL_MESSAGES[first.reason], details=details)


def _warning_body(warning: rules.BookingWarning) -> dict[str, str | None]:
    return {
        "code": warning.code.value,
        "resourceId": str(warning.resource_id) if warning.resource_id else None,
        "againstBookingId": str(warning.against_booking_id) if warning.against_booking_id else None,
    }


def _refuse_unconfirmed(verdict: rules.Verdict, confirmed: Collection[WarningCode]) -> None:
    unconfirmed = verdict.warning_codes - {WarningCode(code) for code in confirmed}
    if unconfirmed:
        raise _refuse(
            "planning_warnings_unconfirmed",
            "The booking warns; book it anyway by confirming every warning.",
            codes=sorted(code.value for code in verdict.warning_codes),
            unconfirmed=sorted(code.value for code in unconfirmed),
            warnings=[_warning_body(warning) for warning in verdict.warnings],
        )


# --- reads ----------------------------------------------------------------------------------


def _load_resources(db: Session, dealership_id: uuid.UUID, resource_ids: Sequence[uuid.UUID]) -> list[Resource]:
    wanted = list(dict.fromkeys(resource_ids))
    if not wanted:
        return []
    found = {
        resource.id: resource
        for resource in db.scalars(
            select(Resource)
            .where(Resource.tenant_id == dealership_id, Resource.id.in_(wanted))
            .execution_options(populate_existing=True)
        )
    }
    if len(found) != len(wanted):
        raise NotFoundError("A resource was not found.")
    return [found[resource_id] for resource_id in wanted]


def _booking_or_404(db: Session, dealership_id: uuid.UUID, booking_id: uuid.UUID) -> Booking:
    booking = db.scalar(
        select(Booking)
        .where(Booking.id == booking_id, Booking.tenant_id == dealership_id)
        .execution_options(populate_existing=True)
    )
    if booking is None:
        raise NotFoundError("The booking was not found.")
    return booking


def _resource_ids_of(db: Session, booking_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(BookingResource.resource_id).where(BookingResource.booking_id == booking_id).order_by(BookingResource.id)
        )
    )


def bookings_of_reference(
    db: Session, *, dealership_id: uuid.UUID, reference_id: uuid.UUID, include_released: bool = False
) -> list[BookingRecord]:
    """Reference R's bookings in the Betrieb, by start. A read; it writes nothing."""

    stmt = select(Booking).where(Booking.tenant_id == dealership_id, Booking.reference_id == reference_id)
    if not include_released:
        stmt = stmt.where(Booking.status == BookingStatus.BOOKED)
    return records_of(db, list(db.scalars(stmt.order_by(Booking.starts_at, Booking.id))))


# --- the shape, the place and the time ------------------------------------------------------


@dataclass(frozen=True)
class _Shape:
    resources: tuple[Resource, ...]
    location_id: uuid.UUID


def _shape(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    kind: BookingKind,
    resources: Sequence[Resource],
    has_reference: bool,
    has_job: bool,
    has_vehicle: bool,
) -> _Shape:
    if kind is BookingKind.WORK:
        workplaces = [resource for resource in resources if resource.kind is ResourceKind.WORKPLACE]
        if len(workplaces) != 1:
            raise _refuse(
                "planning_work_needs_one_workplace", "Work holds exactly one workplace, and people as needed."
            )
        if not (has_job and has_vehicle):
            raise _refuse("planning_work_needs_job_and_vehicle", "Work is booked for a job on a car.")
    elif not resources:
        raise _refuse("planning_booking_needs_a_resource", "A slot or a block holds at least one resource.")
    if kind is not BookingKind.BLOCK and not has_reference:
        raise _refuse("planning_booking_needs_reference", "Work and a drop-off or pick-up slot are booked for a reference.")
    inactive = [resource for resource in resources if not resource.is_active]
    if inactive:
        raise _refuse(
            "planning_resource_inactive",
            "A deactivated resource cannot be booked.",
            resourceIds=[str(resource.id) for resource in inactive],
        )
    locations = {resource.location_id for resource in resources}
    if len(locations) != 1:
        raise _refuse(
            "planning_resources_at_several_locations",
            "A booking's resources are at one location.",
            locationIds=sorted(str(location) for location in locations),
        )
    (location_id,) = locations
    require_active_location(db, dealership_id=dealership_id, location_id=location_id)
    return _Shape(resources=tuple(resources), location_id=location_id)


def _on_grid(instant: dt.datetime, grid: int) -> bool:
    local = instant.astimezone(session_zone())
    return local.second == 0 and local.microsecond == 0 and (local.hour * 60 + local.minute) % grid == 0


def _times(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    kind: BookingKind,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None,
    keep: dt.timedelta | None = None,
) -> tuple[dt.datetime, dt.datetime]:
    """The interval to book: a slot is O7 long; work and a block end where the
    caller says, or keep their length (`keep`, a move); work and slots are on
    the O6 grid, a block on any minute."""

    start = _instant(starts_at, "startsAt")
    grid = grid_minutes(db, dealership_id=dealership_id, location_id=location_id) if kind is not BookingKind.BLOCK else None
    if kind in SLOTS:
        minutes = slot_minutes(db, dealership_id=dealership_id, location_id=location_id)
        end = start + dt.timedelta(minutes=minutes)
        if ends_at is not None and _instant(ends_at, "endsAt") != end:
            raise _refuse(
                "planning_slot_length_fixed",
                "A drop-off or pick-up slot is as long as the business's slot length.",
                slotMinutes=minutes,
            )
    elif ends_at is not None:
        end = _instant(ends_at, "endsAt")
    elif keep is not None:
        end = start + keep
    else:
        raise _refuse("planning_interval_invalid", "A booking ends after it starts.", field="endsAt")
    if start >= end:
        raise _refuse("planning_interval_invalid", "A booking ends after it starts.", field="endsAt")
    if grid is not None:
        off = [field for field, instant in (("startsAt", start), ("endsAt", end)) if not _on_grid(instant, grid)]
        if off:
            raise _refuse(
                "planning_time_off_grid",
                f"Work and slots start and end on the planner's {grid}-minute grid.",
                gridMinutes=grid,
                fields=off,
            )
    return start, end


def _candidate(
    booking_kind: BookingKind,
    dealership_id: uuid.UUID,
    start: dt.datetime,
    end: dt.datetime,
    shape: _Shape,
    *,
    vehicle_id: uuid.UUID | None,
    reference_id: uuid.UUID | None,
    job_id: uuid.UUID | None,
    replaces: uuid.UUID | None = None,
) -> rules.Candidate:
    return rules.Candidate(
        dealership_id=dealership_id,
        kind=booking_kind,
        starts_at=start,
        ends_at=end,
        resources=tuple(rules.ResourceFacts(id=r.id, kind=r.kind) for r in shape.resources),
        vehicle_id=vehicle_id,
        reference_id=reference_id,
        job_id=job_id,
        replaces_booking_id=replaces,
    )


# --- writing --------------------------------------------------------------------------------


def _label(value: str) -> str:
    return value.strip()[:LABEL_LENGTH]


def _audit(booking: Booking, resource_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    return {
        "kind": booking.kind.value,
        "status": booking.status.value,
        "startsAt": booking.starts_at.isoformat(),
        "endsAt": booking.ends_at.isoformat(),
        "resourceIds": [str(resource_id) for resource_id in resource_ids],
        "referenceType": booking.reference_type,
        "referenceId": str(booking.reference_id) if booking.reference_id else None,
        "jobId": str(booking.job_id) if booking.job_id else None,
        "vehicleId": str(booking.vehicle_id) if booking.vehicle_id else None,
        "note": booking.note,
        "replanCause": booking.replan_cause.value if booking.replan_cause else None,
    }


def _write_resources(db: Session, booking: Booking, resources: Sequence[Resource]) -> None:
    db.execute(delete(BookingResource).where(BookingResource.booking_id == booking.id))
    for resource in resources:
        db.add(BookingResource(tenant_id=booking.tenant_id, booking_id=booking.id, resource_id=resource.id))
    db.flush()


def _keep_overruled(
    db: Session,
    *,
    booking: Booking,
    verdict: rules.Verdict,
    change: BookingChange,
    actor_id: uuid.UUID,
    now: dt.datetime,
) -> frozenset[WarningCode]:
    """Each warning the write was made against, twice: a row on the booking
    (the name frozen now) and an audit row (ADR-221 (6), T8)."""

    if not verdict.warnings:
        return frozenset()
    name = actor_name_for(db, dealership_id=booking.tenant_id, user_id=actor_id)
    for warning in dict.fromkeys(verdict.warnings):
        row = OverruledWarning(
            tenant_id=booking.tenant_id,
            booking_id=booking.id,
            code=warning.code,
            resource_id=warning.resource_id,
            against_booking_id=warning.against_booking_id,
            change=change,
            booked_starts_at=booking.starts_at,
            booked_ends_at=booking.ends_at,
            overruled_by=actor_id,
            overruled_by_name=name,
            overruled_at=now,
        )
        db.add(row)
        record_audit_event(
            db,
            entity_type=ENTITY_TYPE,
            entity_id=booking.id,
            tenant_id=booking.tenant_id,
            action="warning_overruled",
            actor_id=actor_id,
            after={
                "code": warning.code.value,
                "resourceId": str(warning.resource_id) if warning.resource_id else None,
                "againstBookingId": str(warning.against_booking_id) if warning.against_booking_id else None,
                "change": change.value,
                "bookedStartsAt": booking.starts_at.isoformat(),
                "bookedEndsAt": booking.ends_at.isoformat(),
                "overruledByName": name,
            },
        )
    db.flush()
    return verdict.warning_codes


def _mark(
    db: Session,
    booking: Booking,
    *,
    cause: ReplanCause,
    actor_id: uuid.UUID | None,
    now: dt.datetime,
    caused_by_booking_id: uuid.UUID | None = None,
    caused_by_resource_id: uuid.UUID | None = None,
) -> None:
    before = booking.replan_cause.value if booking.replan_cause else None
    booking.replan_cause = cause
    booking.replan_since = now
    booking.replan_caused_by_booking_id = caused_by_booking_id
    booking.replan_caused_by_resource_id = caused_by_resource_id
    booking.version += 1
    booking.updated_by = actor_id
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=booking.id,
        tenant_id=booking.tenant_id,
        action="marked_for_replanning",
        actor_id=actor_id,
        before={"replanCause": before},
        after={
            "replanCause": cause.value,
            "causedByBookingId": str(caused_by_booking_id) if caused_by_booking_id else None,
            "causedByResourceId": str(caused_by_resource_id) if caused_by_resource_id else None,
        },
    )


def _clear_mark(booking: Booking) -> None:
    booking.replan_cause = None
    booking.replan_since = None
    booking.replan_caused_by_booking_id = None
    booking.replan_caused_by_resource_id = None


@dataclass(frozen=True)
class _WrongPlace:
    """What stood in the wrong place before the write, to mark only what the write newly put there."""

    hit_by_block: frozenset[uuid.UUID] = frozenset()
    outside_slots: frozenset[uuid.UUID] = frozenset()


def _wrong_place(db: Session, booking: Booking | None, resource_ids: Collection[uuid.UUID]) -> _WrongPlace:
    if booking is None or booking.status is not BookingStatus.BOOKED:
        return _WrongPlace()
    if booking.kind is BookingKind.BLOCK:
        hit = rules.bookings_hit_by_block(
            db,
            dealership_id=booking.tenant_id,
            resource_ids=resource_ids,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            block_id=booking.id,
        )
        return _WrongPlace(hit_by_block=frozenset(other.id for other in hit))
    if booking.kind in SLOTS and booking.reference_id is not None:
        outside = rules.work_outside_slots(db, dealership_id=booking.tenant_id, reference_id=booking.reference_id)
        return _WrongPlace(outside_slots=frozenset(outside))
    return _WrongPlace()


def _mark_wrong_place(
    db: Session, booking: Booking, before: _WrongPlace, *, actor_id: uuid.UUID, now: dt.datetime
) -> list[Booking]:
    """Marks what the write newly put in the wrong place; returns those bookings."""

    db.flush()
    marked: list[Booking] = []
    if booking.kind is BookingKind.BLOCK:
        hit = rules.bookings_hit_by_block(
            db,
            dealership_id=booking.tenant_id,
            resource_ids=_resource_ids_of(db, booking.id),
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            block_id=booking.id,
        )
        for other in hit:
            if other.id not in before.hit_by_block and other.replan_cause is None:
                _mark(db, other, cause=ReplanCause.BLOCKED, actor_id=actor_id, now=now, caused_by_booking_id=booking.id)
                marked.append(other)
    elif booking.kind in SLOTS and booking.reference_id is not None:
        outside = rules.work_outside_slots(db, dealership_id=booking.tenant_id, reference_id=booking.reference_id)
        for work in bookings_by_id(db, dealership_id=booking.tenant_id, ids=outside):
            if work.id not in before.outside_slots and work.replan_cause is None:
                _mark(
                    db,
                    work,
                    cause=ReplanCause.OUTSIDE_DROP_OFF_PICK_UP,
                    actor_id=actor_id,
                    now=now,
                    caused_by_booking_id=outside[work.id],
                )
                marked.append(work)
    db.flush()
    return marked


def _publish_marked(db: Session, marked: Sequence[Booking]) -> tuple[BookingRecord, ...]:
    records = tuple(records_of(db, marked))
    for record in records:
        publish_booking_fact(db, event_type=BOOKING_MARKED_FOR_REPLANNING_EVENT, record=record)
    return records


# --- idempotency ----------------------------------------------------------------------------


def _replay(
    db: Session, *, dealership_id: uuid.UUID, idempotency_key: str | None, path: str, body: dict[str, Any]
) -> BookingOutcome | None:
    """A key seen before returns the booking as it now stands, and what that
    write marked — never a second write."""

    if idempotency_key is None:
        return None
    cached = find_cached_response(db, tenant_id=dealership_id, key=idempotency_key, path=path, body=body)
    if cached is None:
        return None
    stored = cached.response_body
    booking = _booking_or_404(db, dealership_id, uuid.UUID(stored["bookingId"]))
    marked = bookings_by_id(
        db, dealership_id=dealership_id, ids=[uuid.UUID(i) for i in stored.get("markedForReplanning", [])]
    )
    return BookingOutcome(booking=record_of(db, booking), marked_for_replanning=tuple(records_of(db, marked)))


def _finish(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    idempotency_key: str | None,
    path: str,
    body: dict[str, Any],
    outcome: BookingOutcome,
) -> BookingOutcome:
    if idempotency_key is not None:
        store_response(
            db,
            tenant_id=dealership_id,
            key=idempotency_key,
            path=path,
            body=body,
            response_status=200,
            response_body={
                "bookingId": str(outcome.booking.id),
                "markedForReplanning": [str(record.id) for record in outcome.marked_for_replanning],
            },
        )
    db.commit()
    return outcome


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _ids(values: Sequence[uuid.UUID] | None) -> list[str] | None:
    return [str(value) for value in values] if values is not None else None


# --- the writes -----------------------------------------------------------------------------

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _own_transaction(write: Callable[_P, _R]) -> Callable[_P, _R]:
    """The write owns its transaction (ADR-047 pattern B): it commits it, and a
    refusal rolls it back — the booking lock with it."""

    @functools.wraps(write)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        db = args[0] if args else kwargs["db"]
        assert isinstance(db, Session)
        try:
            return write(*args, **kwargs)
        except BaseException:
            db.rollback()
            raise

    return run


@_own_transaction
def book(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID,
    kind: BookingKind,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None,
    resource_ids: Sequence[uuid.UUID],
    reference: Reference | None,
    job: LabelledId | None = None,
    vehicle: LabelledId | None = None,
    confirmed_warnings: Collection[WarningCode] = (),
    idempotency_key: str | None,
) -> BookingOutcome:
    """Books work or a drop-off or pick-up slot (a block: `block`). A slot's
    end is None, or its start plus O7."""

    kind = BookingKind(kind)
    if kind is BookingKind.BLOCK:
        raise ValueError("a block is written by block()")
    if reference is not None and not _REFERENCE_TYPE.fullmatch(reference.type):
        raise ValueError(f"reference type {reference.type!r} is not `<context>.<entity>`")
    path = "planning.book"
    body = {
        "kind": kind.value,
        "startsAt": _iso(starts_at),
        "endsAt": _iso(ends_at),
        "resourceIds": _ids(resource_ids),
        "reference": {"type": reference.type, "id": str(reference.id), "label": reference.label} if reference else None,
        "job": {"id": str(job.id), "label": job.label} if job else None,
        "vehicle": {"id": str(vehicle.id), "label": vehicle.label} if vehicle else None,
        "confirmedWarnings": sorted(WarningCode(code).value for code in confirmed_warnings),
    }
    lock_bookings(db, dealership_id)
    replayed = _replay(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body)
    if replayed is not None:
        return replayed

    resources = _load_resources(db, dealership_id, resource_ids)
    shape = _shape(
        db,
        dealership_id=dealership_id,
        kind=kind,
        resources=resources,
        has_reference=reference is not None,
        has_job=job is not None,
        has_vehicle=vehicle is not None,
    )
    start, end = _times(
        db, dealership_id=dealership_id, location_id=shape.location_id, kind=kind, starts_at=starts_at, ends_at=ends_at
    )
    verdict = rules.judge(
        db,
        _candidate(
            kind,
            dealership_id,
            start,
            end,
            shape,
            vehicle_id=vehicle.id if vehicle else None,
            reference_id=reference.id if reference else None,
            job_id=job.id if job else None,
        ),
    )
    _raise_refusal(verdict)
    _refuse_unconfirmed(verdict, confirmed_warnings)

    now = utcnow()
    booking = Booking(
        tenant_id=dealership_id,
        kind=kind,
        status=BookingStatus.BOOKED,
        starts_at=start,
        ends_at=end,
        reference_type=reference.type if reference else None,
        reference_id=reference.id if reference else None,
        reference_label=_label(reference.label) if reference else None,
        reference_label_refreshed_at=now if reference else None,
        job_id=job.id if job else None,
        job_label=_label(job.label) if job else None,
        job_label_refreshed_at=now if job else None,
        vehicle_id=vehicle.id if vehicle else None,
        vehicle_label=_label(vehicle.label) if vehicle else None,
        vehicle_label_refreshed_at=now if vehicle else None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(booking)
    db.flush()
    _write_resources(db, booking, shape.resources)
    overruled = _keep_overruled(db, booking=booking, verdict=verdict, change=BookingChange.BOOKED, actor_id=actor_id, now=now)
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=booking.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_audit(booking, [r.id for r in shape.resources]),
    )
    marked = _mark_wrong_place(db, booking, _WrongPlace(), actor_id=actor_id, now=now)
    record = record_of(db, booking)
    publish_booking_fact(db, event_type=BOOKING_BOOKED_EVENT, record=record, overruled=overruled)
    outcome = BookingOutcome(booking=record, marked_for_replanning=_publish_marked(db, marked))
    return _finish(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body, outcome=outcome)


@_own_transaction
def block(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID,
    resource_ids: Sequence[uuid.UUID],
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    note: str | None = None,
    idempotency_key: str | None,
) -> BookingOutcome:
    """Blocks one or more resources: always possible, on any minute; the
    bookings it hits are marked «neu einplanen» (cause `blocked`)."""

    cleaned = note.strip() if note is not None else None
    if cleaned is not None and len(cleaned) > NOTE_LENGTH:
        raise _refuse("planning_note_too_long", f"A block's note is at most {NOTE_LENGTH} characters.", maxLength=NOTE_LENGTH)
    path = "planning.block"
    body = {"startsAt": _iso(starts_at), "endsAt": _iso(ends_at), "resourceIds": _ids(resource_ids), "note": note}
    lock_bookings(db, dealership_id)
    replayed = _replay(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body)
    if replayed is not None:
        return replayed

    resources = _load_resources(db, dealership_id, resource_ids)
    shape = _shape(
        db,
        dealership_id=dealership_id,
        kind=BookingKind.BLOCK,
        resources=resources,
        has_reference=False,
        has_job=False,
        has_vehicle=False,
    )
    start, end = _times(
        db,
        dealership_id=dealership_id,
        location_id=shape.location_id,
        kind=BookingKind.BLOCK,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    now = utcnow()
    booking = Booking(
        tenant_id=dealership_id,
        kind=BookingKind.BLOCK,
        status=BookingStatus.BOOKED,
        starts_at=start,
        ends_at=end,
        note=cleaned or None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(booking)
    db.flush()
    _write_resources(db, booking, shape.resources)
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=booking.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_audit(booking, [r.id for r in shape.resources]),
    )
    marked = _mark_wrong_place(db, booking, _WrongPlace(), actor_id=actor_id, now=now)
    record = record_of(db, booking)
    publish_booking_fact(db, event_type=BOOKING_BOOKED_EVENT, record=record)
    outcome = BookingOutcome(booking=record, marked_for_replanning=_publish_marked(db, marked))
    return _finish(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body, outcome=outcome)


def _refuse_released(booking: Booking) -> None:
    if booking.status is BookingStatus.RELEASED:
        raise ConflictError("The booking is released.", details={"reason": "planning_booking_released"})


@_own_transaction
def move(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID,
    booking_id: uuid.UUID,
    expected_version: int,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None = None,
    resource_ids: Sequence[uuid.UUID] | None = None,
    confirmed_warnings: Collection[WarningCode] = (),
    idempotency_key: str | None,
) -> BookingOutcome:
    """Moves a booking in time, onto other resources, or both. Judged as a
    new booking would be, against every booking but itself; clears its
    «neu einplanen» mark."""

    path = f"planning.move:{booking_id}"
    body = {
        "expectedVersion": expected_version,
        "startsAt": _iso(starts_at),
        "endsAt": _iso(ends_at),
        "resourceIds": _ids(resource_ids),
        "confirmedWarnings": sorted(WarningCode(code).value for code in confirmed_warnings),
    }
    lock_bookings(db, dealership_id)
    replayed = _replay(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body)
    if replayed is not None:
        return replayed

    booking = _booking_or_404(db, dealership_id, booking_id)
    _refuse_released(booking)
    check_version(booking.version, expected_version, entity_name="Booking")
    held = _resource_ids_of(db, booking.id)
    resources = _load_resources(db, dealership_id, resource_ids if resource_ids is not None else held)
    shape = _shape(
        db,
        dealership_id=dealership_id,
        kind=booking.kind,
        resources=resources,
        has_reference=booking.reference_id is not None,
        has_job=booking.job_id is not None,
        has_vehicle=booking.vehicle_id is not None,
    )
    start, end = _times(
        db,
        dealership_id=dealership_id,
        location_id=shape.location_id,
        kind=booking.kind,
        starts_at=starts_at,
        ends_at=ends_at,
        keep=booking.ends_at - booking.starts_at,
    )
    verdict = (
        rules.judge(
            db,
            _candidate(
                booking.kind,
                dealership_id,
                start,
                end,
                shape,
                vehicle_id=booking.vehicle_id,
                reference_id=booking.reference_id,
                job_id=booking.job_id,
                replaces=booking.id,
            ),
        )
        if booking.kind is not BookingKind.BLOCK
        else rules.Verdict(refusals=(), warnings=())
    )
    _raise_refusal(verdict)
    _refuse_unconfirmed(verdict, confirmed_warnings)

    before_wrong = _wrong_place(db, booking, held)
    before = _audit(booking, held)
    now = utcnow()
    booking.starts_at = start
    booking.ends_at = end
    _clear_mark(booking)
    booking.version += 1
    booking.updated_by = actor_id
    db.flush()
    if [r.id for r in shape.resources] != held:
        _write_resources(db, booking, shape.resources)
    overruled = _keep_overruled(db, booking=booking, verdict=verdict, change=BookingChange.MOVED, actor_id=actor_id, now=now)
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=booking.id,
        tenant_id=dealership_id,
        action="update",
        actor_id=actor_id,
        before=before,
        after=_audit(booking, [r.id for r in shape.resources]),
    )
    marked = _mark_wrong_place(db, booking, before_wrong, actor_id=actor_id, now=now)
    record = record_of(db, booking)
    publish_booking_fact(db, event_type=BOOKING_MOVED_EVENT, record=record, overruled=overruled)
    outcome = BookingOutcome(booking=record, marked_for_replanning=_publish_marked(db, marked))
    return _finish(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body, outcome=outcome)


@_own_transaction
def release(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    booking_id: uuid.UUID,
    expected_version: int,
    idempotency_key: str | None,
) -> BookingRecord:
    """Releases a booking: it holds nothing from now on, and is kept."""

    path = f"planning.release:{booking_id}"
    body = {"expectedVersion": expected_version}
    lock_bookings(db, dealership_id)
    replayed = _replay(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body)
    if replayed is not None:
        return replayed.booking

    booking = _booking_or_404(db, dealership_id, booking_id)
    _refuse_released(booking)
    check_version(booking.version, expected_version, entity_name="Booking")
    held = _resource_ids_of(db, booking.id)
    before = _audit(booking, held)
    booking.status = BookingStatus.RELEASED
    booking.released_at = utcnow()
    booking.released_by = actor_id
    _clear_mark(booking)
    booking.version += 1
    booking.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type=ENTITY_TYPE,
        entity_id=booking.id,
        tenant_id=dealership_id,
        action="release",
        actor_id=actor_id,
        before=before,
        after=_audit(booking, held),
    )
    record = record_of(db, booking)
    publish_booking_fact(db, event_type=BOOKING_RELEASED_EVENT, record=record)
    outcome = BookingOutcome(booking=record, marked_for_replanning=())
    return _finish(db, dealership_id=dealership_id, idempotency_key=idempotency_key, path=path, body=body, outcome=outcome).booking


def mark_bookings_of_deactivated_resource(
    db: Session, *, dealership_id: uuid.UUID, resource_id: uuid.UUID, actor_id: uuid.UUID | None
) -> list[Booking]:
    """Deactivating a resource is allowed while it holds bookings to come
    (Anto, 2026-11-25): each booked, not yet ended booking that holds it —
    not a block, and not one already marked — is marked «neu einplanen»
    (cause `resource_deactivated`), with its event. Takes the booking lock;
    the caller commits."""

    lock_bookings(db, dealership_id)
    now = utcnow()
    holding = db.scalars(
        select(Booking)
        .join(BookingResource, BookingResource.booking_id == Booking.id)
        .where(
            Booking.tenant_id == dealership_id,
            BookingResource.resource_id == resource_id,
            Booking.status == BookingStatus.BOOKED,
            Booking.kind != BookingKind.BLOCK,
            Booking.ends_at > now,
            Booking.replan_cause.is_(None),
        )
        .order_by(Booking.starts_at, Booking.id)
        .execution_options(populate_existing=True)
    )
    marked = list(holding)
    for booking in marked:
        _mark(
            db,
            booking,
            cause=ReplanCause.RESOURCE_DEACTIVATED,
            actor_id=actor_id,
            now=now,
            caused_by_resource_id=resource_id,
        )
    db.flush()
    _publish_marked(db, marked)
    return marked
