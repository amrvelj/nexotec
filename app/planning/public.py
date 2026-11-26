"""What other contexts and the composition roots may use of planning.

KAN-808 PR-1a: the exporter the composition root registers. PR-1b: the
nightly refreshes of a resource's user and location names, and their ages,
for the worker's daily jobs and heartbeat. PR-1c: the bookings — functions,
frozen dataclasses of plain values and enums only, never a mapped class
(ADR-162). Callers: the contexts above planning (ADR-131 as amended by
KAN-804); planning itself calls platform only.

The writes — `book`, `move`, `release`, `block` — each commit their own
transaction (ADR-047 pattern B) and take an idempotency key: a key seen
before returns the booking as it now stands and writes nothing again. A
caller calls them outside its own transaction (rule 12) and compensates on
its side. Each takes the Betrieb's booking lock first and judges the
booking with the one rule service (ADR-221 (1), (2)):

- refused, 409 with `bookingIds`: `planning_car_at_another_workplace`,
  `planning_workplace_taken` (check 16), `planning_person_twice_on_job`
  (check 17);
- warned, 422 `planning_warnings_unconfirmed` listing each warning
  (`{code, resourceId, againstBookingId}`), until the caller re-sends with
  `confirmed_warnings` holding each of them as a `ConfirmedWarning` —
  confirmation is per warning, never per code, so a warning that appears
  between the two calls refuses again, whatever its code:
  `outside_working_hours` (check 18), `before_drop_off`, `after_pick_up`,
  `resource_blocked`. A booking made against a warning keeps it, with the
  name and the time (`BookingRecord.overruled_warnings`) and an audit row.

Every change raises an outbox event: `planning.booking.booked`, `.moved`,
`.released`, `.marked_for_replanning`, one payload shape
(`services/booking_facts.py`). `bookings_of_reference` is a read.
`register_settings` declares O6 and O7 with platform's settings store; the
composition root `app/setting_registry.py` calls it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Sequence

from sqlalchemy.orm import Session

from app.planning.models.booking import BookingChange, BookingKind, BookingStatus, ReplanCause, WarningCode
from app.planning.models.resource import ResourceKind
from app.planning.services import bookings as _bookings
from app.planning.services.booking_facts import (
    BOOKING_BOOKED_EVENT,
    BOOKING_MARKED_FOR_REPLANNING_EVENT,
    BOOKING_MOVED_EVENT,
    BOOKING_RELEASED_EVENT,
)
from app.planning.services.booking_records import (
    BookedResource,
    BookingOutcome,
    BookingRecord,
    ConfirmedWarning,
    LabelledId,
    OverruledWarningRecord,
    Reference,
    ReplanMark,
)
from app.planning.services.data_export import PlanningExporter
from app.planning.services.labels import (
    oldest_location_label_age_seconds,
    oldest_person_label_age_seconds,
    refresh_location_labels,
    refresh_person_labels,
)
from app.planning.services.planner_settings import register_settings as _register_settings


def book(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID,
    kind: BookingKind,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None,
    resource_ids: Sequence[uuid.UUID],
    reference: Reference,
    job: LabelledId | None = None,
    vehicle: LabelledId | None = None,
    confirmed_warnings: Collection[ConfirmedWarning] = (),
    idempotency_key: str,
) -> BookingOutcome:
    """Books work (one workplace and any people, for `job` on `vehicle`) or a
    drop-off or pick-up slot (any one or more resources; `ends_at` None — it
    is O7 long) for `reference`. Commits."""

    return _bookings.book(
        db,
        dealership_id=dealership_id,
        actor_id=actor_id,
        kind=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        resource_ids=resource_ids,
        reference=reference,
        job=job,
        vehicle=vehicle,
        confirmed_warnings=confirmed_warnings,
        idempotency_key=idempotency_key,
    )


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
    confirmed_warnings: Collection[ConfirmedWarning] = (),
    idempotency_key: str,
) -> BookingOutcome:
    """Moves a booking in time and/or onto other resources (None: the same
    ones). Without `ends_at` a slot is O7 long, work and a block keep their
    length. Clears «neu einplanen». Commits."""

    return _bookings.move(
        db,
        dealership_id=dealership_id,
        actor_id=actor_id,
        booking_id=booking_id,
        expected_version=expected_version,
        starts_at=starts_at,
        ends_at=ends_at,
        resource_ids=resource_ids,
        confirmed_warnings=confirmed_warnings,
        idempotency_key=idempotency_key,
    )


def release(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    booking_id: uuid.UUID,
    expected_version: int,
    idempotency_key: str,
) -> BookingRecord:
    """Releases a booking (it holds nothing and is kept). Commits."""

    return _bookings.release(
        db,
        dealership_id=dealership_id,
        actor_id=actor_id,
        booking_id=booking_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )


def block(
    db: Session,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID,
    resource_ids: Sequence[uuid.UUID],
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    note: str | None = None,
    idempotency_key: str,
) -> BookingOutcome:
    """Blocks one or more resources, on any minute, with an optional note (at
    most 200 characters). Always possible; the bookings it hits are marked
    «neu einplanen». Commits."""

    return _bookings.block(
        db,
        dealership_id=dealership_id,
        actor_id=actor_id,
        resource_ids=resource_ids,
        starts_at=starts_at,
        ends_at=ends_at,
        note=note,
        idempotency_key=idempotency_key,
    )


def bookings_of_reference(
    db: Session, *, dealership_id: uuid.UUID, reference_id: uuid.UUID, include_released: bool = False
) -> list[BookingRecord]:
    """Reference R's bookings in the Betrieb, by start. A read."""

    return _bookings.bookings_of_reference(
        db, dealership_id=dealership_id, reference_id=reference_id, include_released=include_released
    )


def register_settings() -> None:
    """Declares O6 `planning.grid_minutes` and O7 `planning.slot_minutes`."""

    _register_settings()


__all__ = [
    "BOOKING_BOOKED_EVENT",
    "BOOKING_MARKED_FOR_REPLANNING_EVENT",
    "BOOKING_MOVED_EVENT",
    "BOOKING_RELEASED_EVENT",
    "BookedResource",
    "BookingChange",
    "BookingKind",
    "BookingOutcome",
    "BookingRecord",
    "BookingStatus",
    "ConfirmedWarning",
    "LabelledId",
    "OverruledWarningRecord",
    "PlanningExporter",
    "Reference",
    "ReplanCause",
    "ReplanMark",
    "ResourceKind",
    "WarningCode",
    "block",
    "book",
    "bookings_of_reference",
    "move",
    "oldest_location_label_age_seconds",
    "oldest_person_label_age_seconds",
    "refresh_location_labels",
    "refresh_person_labels",
    "register_settings",
    "release",
]
