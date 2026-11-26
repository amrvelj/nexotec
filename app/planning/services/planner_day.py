"""One day of one location as the planner page shows it (KAN-809; PL1).

The day is the Europe/Zurich calendar day: `[local midnight, next local
midnight)` as instants, so a day of 23 or 25 hours (the clock change) is what
it is. Its bookings are every booking not released that holds at least one
resource of the location (active or not: a booking on a deactivated resource
carries its «neu einplanen» mark and must stay visible) and overlaps the day.
A read: it writes nothing and takes no lock.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.relative_dates import session_zone
from app.planning.models.booking import Booking, BookingResource, BookingStatus
from app.planning.models.resource import Resource
from app.planning.services.booking_records import BookingRecord, records_of
from app.planning.services.planner_settings import grid_minutes_or_none
from app.planning.services.platform_refs import location_belongs_to


@dataclass(frozen=True)
class PlannerDay:
    date: dt.date
    location_id: uuid.UUID
    starts_at: dt.datetime
    ends_at: dt.datetime
    grid_minutes: int | None
    bookings: list[BookingRecord]


def day_window(date: dt.date, zone: ZoneInfo | None = None) -> tuple[dt.datetime, dt.datetime]:
    """The local calendar day as UTC instants, half-open."""

    zone = zone or session_zone()
    start = dt.datetime.combine(date, dt.time.min, tzinfo=zone)
    end = dt.datetime.combine(date + dt.timedelta(days=1), dt.time.min, tzinfo=zone)
    return start.astimezone(dt.UTC), end.astimezone(dt.UTC)


def planner_day(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID, date: dt.date) -> PlannerDay:
    """404 for a location that is not one of the Betrieb's (another Betrieb's
    reads the same: rule 7)."""

    if not location_belongs_to(db, dealership_id=dealership_id, location_id=location_id):
        raise NotFoundError("Not found.")
    starts_at, ends_at = day_window(date)
    at_location = (
        select(BookingResource.booking_id)
        .join(Resource, (Resource.id == BookingResource.resource_id) & (Resource.tenant_id == BookingResource.tenant_id))
        .where(BookingResource.tenant_id == dealership_id, Resource.location_id == location_id)
    )
    bookings = list(
        db.scalars(
            select(Booking)
            .where(
                Booking.tenant_id == dealership_id,
                Booking.status == BookingStatus.BOOKED,
                Booking.starts_at < ends_at,
                Booking.ends_at > starts_at,
                Booking.id.in_(at_location),
            )
            .order_by(Booking.starts_at, Booking.id)
        )
    )
    return PlannerDay(
        date=date,
        location_id=location_id,
        starts_at=starts_at,
        ends_at=ends_at,
        grid_minutes=grid_minutes_or_none(db, dealership_id=dealership_id, location_id=location_id),
        bookings=records_of(db, bookings),
    )
