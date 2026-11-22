"""A location as a channel publishes it (KAN-226 PR-3; Dealer Administration
PRD FR-A-35, D-A-32): the one read a mirroring channel — Google Business
Profile first — takes of a site's current state when it handles one of
Platform's location facts (`platform.location.updated`,
`platform.location.schedule_changed`) or the Betrieb's own
(`platform.dealership.updated`, KAN-785: its website).

Plain values only (ADR-162): never a mapped class crosses `platform.public`.
Tenant-scoped: a location of another Betrieb reads as `None`, as a missing one
does, so a consumer cannot learn that it exists.

What it holds — and nothing internal (the contact person, the lead e-mail, the
letterhead and bank-account overrides never leave Platform, as in the facts'
payloads):

- the site's name, active flag, address block and phone; the website is the
  Betrieb's own (`dealership.website`, Business screen — the publishing
  profile owns no second copy of it);
- its opening hours per department, as stored (one row per department and
  open weekday, a second interval for the lunch break);
- the days it is closed within a horizon from `today`: its own closures and
  the observed holidays of its holiday calendar, each day once.

Its publishing imagery is not read here yet: the push consumers run in the
worker, which cannot read the object store's disk in production (KAN-784).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.holiday_calendar import Holiday
from app.platform.organisation.models.location_schedule import LocationClosure, LocationOpeningHours
from app.platform.organisation.services.holidays import MAX_YEAR, MIN_YEAR, dates_in_year

# How far ahead closed days are listed: a channel shows the coming year.
CLOSED_DAYS_HORIZON = dt.timedelta(days=365)


@dataclasses.dataclass(frozen=True)
class ChannelOpeningHours:
    department: str  # `sales` | `service` | `parts`
    weekday: int  # ISO 8601: 1 = Monday … 7 = Sunday
    opens_at: dt.time
    closes_at: dt.time
    second_opens_at: dt.time | None
    second_closes_at: dt.time | None


@dataclasses.dataclass(frozen=True)
class LocationChannelFacts:
    dealership_id: uuid.UUID
    location_id: uuid.UUID
    name: str
    is_active: bool
    address_street: str | None
    address_house_number: str | None
    address_postal_code: str | None
    address_locality: str | None
    address_canton: str | None
    address_country: str | None
    phone: str | None
    website: str | None
    opening_hours: tuple[ChannelOpeningHours, ...]
    closed_days: tuple[dt.date, ...]


def _location(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> Location | None:
    location = db.get(Location, location_id)
    if location is None or location.tenant_id != dealership_id:
        return None
    return location


def closed_days_until(today: dt.date) -> dt.date:
    """The last day (inclusive) a read made on `today` lists closed days
    for — what a channel pushed on `today` knows of (KAN-226 PR-4's drift
    check compares no later day). Pure; reads nothing."""

    return today + CLOSED_DAYS_HORIZON


def _closed_days(db: Session, location: Location, *, today: dt.date) -> tuple[dt.date, ...]:
    until = closed_days_until(today)
    days: set[dt.date] = set()
    closures = db.scalars(
        select(LocationClosure).where(
            LocationClosure.location_id == location.id,
            LocationClosure.ends_on >= today,
            LocationClosure.starts_on <= until,
        )
    ).all()
    for closure in closures:
        day = max(closure.starts_on, today)
        while day <= min(closure.ends_on, until):
            days.update((day,))
            day += dt.timedelta(days=1)
    if location.holiday_calendar_id is not None:
        holidays = list(db.scalars(select(Holiday).where(Holiday.calendar_id == location.holiday_calendar_id)).all())
        for year in range(max(today.year, MIN_YEAR), min(until.year, MAX_YEAR) + 1):
            days.update(d.date for d in dates_in_year(holidays, year) if today <= d.date <= until)
    return tuple(sorted(days))


def location_channel_facts(
    db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID, today: dt.date
) -> LocationChannelFacts | None:
    """The site's published state as it now stands; `None` when the location
    is not one of this Betrieb's."""

    location = _location(db, dealership_id=dealership_id, location_id=location_id)
    if location is None:
        return None
    dealership = db.get(Dealership, dealership_id)
    hours = db.scalars(
        select(LocationOpeningHours)
        .where(LocationOpeningHours.location_id == location.id)
        .order_by(LocationOpeningHours.department, LocationOpeningHours.weekday)
    ).all()
    return LocationChannelFacts(
        dealership_id=dealership_id,
        location_id=location.id,
        name=location.name,
        is_active=bool(location.is_active),
        address_street=location.address_street,
        address_house_number=location.address_house_number,
        address_postal_code=location.address_postal_code,
        address_locality=location.address_locality,
        address_canton=location.address_canton,
        address_country=location.address_country,
        phone=location.phone,
        website=dealership.website if dealership is not None else None,
        opening_hours=tuple(
            ChannelOpeningHours(
                department=row.department.value,
                weekday=row.weekday,
                opens_at=row.opens_at,
                closes_at=row.closes_at,
                second_opens_at=row.second_opens_at,
                second_closes_at=row.second_closes_at,
            )
            for row in hours
        ),
        closed_days=_closed_days(db, location, today=today),
    )
