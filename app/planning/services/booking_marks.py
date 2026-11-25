"""«Neu einplanen» (KAN-808 PR-1c; ADR-221): marking a booking that a later
change left in the wrong place, and what stood there before a write.

- A block marks the booked non-block bookings it newly hits on its resources
  (cause `blocked`, the block named).
- A booked or moved drop-off or pick-up slot marks R's work it newly leaves
  outside it (cause `outside_drop_off_pick_up`, the slot named).
- Deactivating a resource, or moving it to another location, marks its
  bookings not yet ended (`resource_deactivated`, `resource_moved`; Anto,
  2026-11-25), the resource named.

What was already in the wrong place before the write — booked knowingly
against a warning — is not marked again (`wrong_place` before, compared
after); a booking already marked keeps its first cause; a block is never
marked. Each marked booking gets `version + 1`, an audit row and its own
`planning.booking.marked_for_replanning`. Every function here runs inside its
caller's transaction, under the Betrieb's booking lock; none commits.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.planning.models.booking import Booking, BookingKind, BookingResource, BookingStatus, ReplanCause
from app.planning.services import booking_rules as rules
from app.planning.services.booking_facts import BOOKING_MARKED_FOR_REPLANNING_EVENT, publish_booking_fact
from app.planning.services.booking_records import BookingRecord, bookings_by_id, records_of, resource_ids_of
from app.planning.services.locks import lock_bookings

ENTITY_TYPE = "planning_booking"
SLOTS = (BookingKind.DROP_OFF, BookingKind.PICK_UP)


def mark(
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


def clear_mark(booking: Booking) -> None:
    booking.replan_cause = None
    booking.replan_since = None
    booking.replan_caused_by_booking_id = None
    booking.replan_caused_by_resource_id = None


@dataclass(frozen=True)
class WrongPlace:
    """What stood in the wrong place before the write, to mark only what the write newly put there."""

    hit_by_block: frozenset[uuid.UUID] = frozenset()
    outside_slots: frozenset[uuid.UUID] = frozenset()


def wrong_place(db: Session, booking: Booking | None, resource_ids: Collection[uuid.UUID]) -> WrongPlace:
    if booking is None or booking.status is not BookingStatus.BOOKED:
        return WrongPlace()
    if booking.kind is BookingKind.BLOCK:
        hit = rules.bookings_hit_by_block(
            db,
            dealership_id=booking.tenant_id,
            resource_ids=resource_ids,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            block_id=booking.id,
        )
        return WrongPlace(hit_by_block=frozenset(other.id for other in hit))
    if booking.kind in SLOTS and booking.reference_id is not None:
        outside = rules.work_outside_slots(db, dealership_id=booking.tenant_id, reference_id=booking.reference_id)
        return WrongPlace(outside_slots=frozenset(outside))
    return WrongPlace()


def mark_wrong_place(
    db: Session, booking: Booking, before: WrongPlace, *, actor_id: uuid.UUID, now: dt.datetime
) -> list[Booking]:
    """Marks what the write newly put in the wrong place; returns those bookings."""

    db.flush()
    marked: list[Booking] = []
    if booking.kind is BookingKind.BLOCK:
        hit = rules.bookings_hit_by_block(
            db,
            dealership_id=booking.tenant_id,
            resource_ids=resource_ids_of(db, booking.id),
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            block_id=booking.id,
        )
        for other in hit:
            if other.id not in before.hit_by_block and other.replan_cause is None:
                mark(db, other, cause=ReplanCause.BLOCKED, actor_id=actor_id, now=now, caused_by_booking_id=booking.id)
                marked.append(other)
    elif booking.kind in SLOTS and booking.reference_id is not None:
        outside = rules.work_outside_slots(db, dealership_id=booking.tenant_id, reference_id=booking.reference_id)
        for work in bookings_by_id(db, dealership_id=booking.tenant_id, ids=outside):
            if work.id not in before.outside_slots and work.replan_cause is None:
                mark(
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


def publish_marked(db: Session, marked: Sequence[Booking]) -> tuple[BookingRecord, ...]:
    records = tuple(records_of(db, marked))
    for record in records:
        publish_booking_fact(db, event_type=BOOKING_MARKED_FOR_REPLANNING_EVENT, record=record)
    return records


RESOURCE_CAUSES = (ReplanCause.RESOURCE_DEACTIVATED, ReplanCause.RESOURCE_MOVED)


def mark_bookings_of_resource(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    resource_id: uuid.UUID,
    cause: ReplanCause,
    actor_id: uuid.UUID | None,
) -> list[Booking]:
    """A resource deactivated, or moved to another location, while it holds
    bookings to come — both allowed (Anto, 2026-11-25): each booked, not yet
    ended booking that holds it — not a block, and not one already marked — is
    marked «neu einplanen» with `cause`, the resource named, with its event.
    Takes the booking lock; the caller commits."""

    if cause not in RESOURCE_CAUSES:
        raise ValueError(f"{cause!r} is no cause a resource's change gives")
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
        mark(
            db,
            booking,
            cause=cause,
            actor_id=actor_id,
            now=now,
            caused_by_resource_id=resource_id,
        )
    db.flush()
    publish_marked(db, marked)
    return marked
