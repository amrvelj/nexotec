"""The planner page's API (KAN-809; slice 1, section 3.8; PL1, PL2, PL4).

A booking as the page reads it, the day of one location, and the three writes
the page makes: change a booking (move), release it, block resources. Work and
the drop-off and pick-up slots are booked from the order (KAN-812; Anto,
2026-11-26), never here.
"""

import datetime as dt
import uuid

from pydantic import ConfigDict, Field

from app.core.schemas import CamelModel
from app.planning.models.booking import (
    NOTE_LENGTH,
    BookingChange,
    BookingKind,
    BookingStatus,
    ReplanCause,
    WarningCode,
)
from app.planning.models.resource import ResourceKind


class BookedResourceRead(CamelModel):
    resource_id: uuid.UUID
    kind: ResourceKind
    label: str


class ReferenceRead(CamelModel):
    # The owning context and entity, e.g. `aftersales.order`.
    type: str
    id: uuid.UUID
    label: str


class LabelledIdRead(CamelModel):
    id: uuid.UUID
    label: str


class ReplanMarkRead(CamelModel):
    """«Neu einplanen»: why, since when, and what caused it."""

    cause: ReplanCause
    since: dt.datetime
    caused_by_booking_id: uuid.UUID | None
    caused_by_resource_id: uuid.UUID | None


class OverruledWarningRead(CamelModel):
    """A warning booked against: kept with the name and the time (decision 22)."""

    code: WarningCode
    resource_id: uuid.UUID | None
    against_booking_id: uuid.UUID | None
    change: BookingChange
    booked_starts_at: dt.datetime
    booked_ends_at: dt.datetime
    overruled_by: uuid.UUID
    overruled_by_name: str
    overruled_at: dt.datetime


class BookingRead(CamelModel):
    id: uuid.UUID
    kind: BookingKind
    status: BookingStatus
    starts_at: dt.datetime
    ends_at: dt.datetime
    resources: list[BookedResourceRead]
    reference: ReferenceRead | None
    job: LabelledIdRead | None
    vehicle: LabelledIdRead | None
    note: str | None
    replan: ReplanMarkRead | None
    overruled_warnings: list[OverruledWarningRead]
    version: int


class BookingOutcomeRead(CamelModel):
    """A booking written, and the bookings the write marked «neu einplanen»."""

    booking: BookingRead
    marked_for_replanning: list[BookingRead]


class PlannerDayRead(CamelModel):
    """One day of one location: its bookings in the Europe/Zurich day
    `[starts_at, ends_at)` — every booking not released that holds a resource
    of the location and overlaps the day — and the grid of setting O6 there
    (null while it is not set)."""

    date: dt.date
    location_id: uuid.UUID
    starts_at: dt.datetime
    ends_at: dt.datetime
    grid_minutes: int | None
    bookings: list[BookingRead]


class ConfirmedWarningWrite(CamelModel):
    """A warning the page showed and the user books against, exactly as the
    422 `planning_warnings_unconfirmed` listed it."""

    model_config = ConfigDict(extra="forbid")

    code: WarningCode
    resource_id: uuid.UUID | None = None
    against_booking_id: uuid.UUID | None = None


class BookingMove(CamelModel):
    """Change a booking: its time, its resources, or both. Without `endsAt` a
    slot is O7 long and work or a block keeps its length; without
    `resourceIds` it keeps its resources."""

    model_config = ConfigDict(extra="forbid")

    starts_at: dt.datetime
    ends_at: dt.datetime | None = None
    resource_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    confirmed_warnings: list[ConfirmedWarningWrite] = Field(default_factory=list)


class BlockCreate(CamelModel):
    """Block people and/or workplaces, with an optional note (Anto, 2026-11-25)."""

    model_config = ConfigDict(extra="forbid")

    resource_ids: list[uuid.UUID] = Field(min_length=1)
    starts_at: dt.datetime
    ends_at: dt.datetime
    note: str | None = Field(default=None, max_length=NOTE_LENGTH)
