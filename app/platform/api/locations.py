"""Locations (KAN-204; Dealer Administration PRD › Locations; D-A-02, D-A-27,
FR-A-03, A-RULE-1) and the cantonal holiday calendars they pick from.

- `/v1/dealerships/{id}/locations` — list, create, read, PATCH. No DELETE:
  a site is deactivated (`isActive: false`), never deleted — historic
  documents reference it by id. The last active location cannot be
  deactivated (FR-A-03).
- `…/locations/{location_id}/opening-hours` — GET and PUT the whole week
  (If-Match on the location's version: the hours are the location's own).
- `…/locations/{location_id}/closures` — the Betrieb's own closures; each
  closure is versioned and takes If-Match on PATCH and DELETE.
- `/v1/holiday-calendars` — platform reference data, read by any signed-in
  user; `…/{calendar_id}/dates?year=` turns a calendar's rules into dates.

Matrix row `locations`: any role reads, the manager (and platform staff)
writes. The tenant is the token's: a path id that is not the caller's
dealership is a 404 (rule 7), and so is another Betrieb's location. A
location is never a scoping boundary — no endpoint filters by it.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.errors import NotFoundError
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import require_banking_read, require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.dealership import Location
from app.platform.models.holiday_calendar import Holiday, HolidayCalendar
from app.platform.models.location_schedule import LocationOpeningHours
from app.platform.schemas.location import (
    ClosureCreate,
    ClosureList,
    ClosureRead,
    ClosureUpdate,
    HolidayCalendarList,
    HolidayCalendarRead,
    HolidayDateList,
    HolidayDateRead,
    HolidayRead,
    LocationCreate,
    LocationList,
    LocationRead,
    LocationUpdate,
    OpeningHoursRead,
    OpeningHoursRow,
    OpeningHoursUpdate,
)
from app.platform.services import dealership as dealership_service
from app.platform.services import holidays as holiday_service
from app.platform.services import location as location_service

router = APIRouter(tags=["locations"], route_class=IdempotentRoute)

# Holidays are calendar days in Switzerland, not in the server's zone.
_SWISS_TIME = ZoneInfo("Europe/Zurich")


def _reads_banking(principal: Principal) -> bool:
    """ADR-100: the bank-account override exists only for the roles that read
    banking; for anyone else the field reads null, as if unset."""

    try:
        require_banking_read()(principal)
    except NotFoundError:
        return False
    return True


def _location_read(location: Location, principal: Principal) -> LocationRead:
    read = LocationRead.model_validate(
        {
            **{field: getattr(location, field) for field in LocationRead.model_fields if hasattr(location, field)},
            "dealership_id": location.tenant_id,
            "location_types": list(location.location_types or []),
            "geo_lat": None if location.geo_lat is None else float(location.geo_lat),
            "geo_lng": None if location.geo_lng is None else float(location.geo_lng),
        }
    )
    if not _reads_banking(principal):
        read.bank_account_override_id = None
    return read


def _hours_read(location: Location, rows: list[LocationOpeningHours]) -> OpeningHoursRead:
    return OpeningHoursRead(
        location_id=location.id,
        location_version=location.version,
        per_department=location_service.hours_differ_per_department(rows),
        hours=[OpeningHoursRow.model_validate(row, from_attributes=True) for row in rows],
    )


def _closure_read(closure) -> ClosureRead:
    return ClosureRead.model_validate(closure, from_attributes=True)


def _location(db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID, *, for_update: bool = False) -> Location:
    lookup = location_service.get_location_for_update_or_404 if for_update else location_service.get_location_or_404
    return lookup(db, dealership_id=dealership_id, location_id=location_id)


# --- Locations ------------------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/locations", response_model=LocationList)
def list_locations(
    dealership_id: uuid.UUID,
    active: bool | None = Query(default=None, description="true: active sites only; false: deactivated only."),
    principal: Principal = Depends(require_read("locations")),
    db: Session = Depends(get_db),
):
    """The Betrieb's sites, active and deactivated (the Users screen's picker
    offers only the active ones to a new assignment)."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    rows = location_service.list_locations(db, dealership_id=dealership_id, active=active)
    return LocationList(items=[_location_read(row, principal) for row in rows])


@router.post("/dealerships/{dealership_id}/locations", response_model=LocationRead, status_code=201)
def create_location(
    dealership_id: uuid.UUID,
    body: LocationCreate,
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    location = location_service.create_location(
        db, dealership_id=dealership_id, data=body, actor_id=principal.user_id
    )
    return _location_read(location, principal)


@router.get("/dealerships/{dealership_id}/locations/{location_id}", response_model=LocationRead)
def get_location(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    principal: Principal = Depends(require_read("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    return _location_read(_location(db, dealership_id, location_id), principal)


@router.patch("/dealerships/{dealership_id}/locations/{location_id}", response_model=LocationRead)
def update_location(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: LocationUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    """PATCH; `isActive: false` deactivates (never a delete), refused for the
    last active location (409 `last_active_location`, FR-A-03)."""

    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id, for_update=True)
    check_version(location.version, if_match, entity_name="Location")
    location = location_service.update_location(db, location=location, data=body, actor_id=principal.user_id)
    return _location_read(location, principal)


# --- Opening hours ------------------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/locations/{location_id}/opening-hours", response_model=OpeningHoursRead)
def get_opening_hours(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    principal: Principal = Depends(require_read("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id)
    return _hours_read(location, location_service.list_opening_hours(db, location_id=location.id))


@router.put("/dealerships/{dealership_id}/locations/{location_id}/opening-hours", response_model=OpeningHoursRead)
def set_opening_hours(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: OpeningHoursUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    """Replaces the week. If-Match is the location's version, which the PUT
    bumps — the hours are part of the location."""

    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id, for_update=True)
    check_version(location.version, if_match, entity_name="Location")
    rows = location_service.set_opening_hours(db, location=location, data=body, actor_id=principal.user_id)
    return _hours_read(location, rows)


# --- Closures -------------------------------------------------------------------------------


@router.get("/dealerships/{dealership_id}/locations/{location_id}/closures", response_model=ClosureList)
def list_closures(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    principal: Principal = Depends(require_read("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id)
    return ClosureList(items=[_closure_read(c) for c in location_service.list_closures(db, location_id=location.id)])


@router.post(
    "/dealerships/{dealership_id}/locations/{location_id}/closures", response_model=ClosureRead, status_code=201
)
def create_closure(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    body: ClosureCreate,
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id)
    return _closure_read(
        location_service.create_closure(db, location=location, data=body, actor_id=principal.user_id)
    )


@router.patch(
    "/dealerships/{dealership_id}/locations/{location_id}/closures/{closure_id}", response_model=ClosureRead
)
def update_closure(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    closure_id: uuid.UUID,
    body: ClosureUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id)
    closure = location_service.get_closure_or_404(
        db, location_id=location.id, closure_id=closure_id, for_update=True
    )
    check_version(closure.version, if_match, entity_name="LocationClosure")
    return _closure_read(
        location_service.update_closure(db, location=location, closure=closure, data=body, actor_id=principal.user_id)
    )


@router.delete("/dealerships/{dealership_id}/locations/{location_id}/closures/{closure_id}", status_code=204)
def delete_closure(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID,
    closure_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("locations")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    location = _location(db, dealership_id, location_id)
    closure = location_service.get_closure_or_404(
        db, location_id=location.id, closure_id=closure_id, for_update=True
    )
    check_version(closure.version, if_match, entity_name="LocationClosure")
    location_service.delete_closure(db, location=location, closure=closure, actor_id=principal.user_id)
    return Response(status_code=204)


# --- Holiday calendars (platform reference data) ----------------------------------------------


def _holiday_read(holiday: Holiday) -> HolidayRead:
    return HolidayRead.model_validate(holiday, from_attributes=True)


def _calendar_read(calendar: HolidayCalendar, holidays: list[Holiday]) -> HolidayCalendarRead:
    return HolidayCalendarRead(
        id=calendar.id,
        code=calendar.code,
        canton=calendar.canton,
        label_de=calendar.label_de,
        label_fr=calendar.label_fr,
        label_it=calendar.label_it,
        label_en=calendar.label_en,
        source=calendar.source,
        holidays=[_holiday_read(h) for h in holidays],
    )


@router.get("/holiday-calendars", response_model=HolidayCalendarList)
def list_holiday_calendars(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Every canton's calendar (a canton whose holidays differ by region has
    one per region), each with its holidays as rules. Reference data: the
    same for every Betrieb."""

    calendars = holiday_service.list_calendars(db)
    holidays = holiday_service.holidays_by_calendar(db, [c.id for c in calendars])
    return HolidayCalendarList(items=[_calendar_read(c, holidays[c.id]) for c in calendars])


@router.get("/holiday-calendars/{calendar_id}/dates", response_model=HolidayDateList)
def list_holiday_dates(
    calendar_id: uuid.UUID,
    year: int | None = Query(
        default=None,
        ge=holiday_service.MIN_YEAR,
        le=holiday_service.MAX_YEAR,
        description="Defaults to the current year in Switzerland.",
    ),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The calendar's holidays in one year, in date order."""

    if year is None:
        year = dt.datetime.now(_SWISS_TIME).year
    calendar = holiday_service.get_calendar_or_404(db, calendar_id)
    holidays = holiday_service.holidays_by_calendar(db, [calendar.id])[calendar.id]
    return HolidayDateList(
        calendar_id=calendar.id,
        year=year,
        items=[
            HolidayDateRead(
                date=entry.date,
                code=entry.holiday.code,
                label_de=entry.holiday.label_de,
                label_fr=entry.holiday.label_fr,
                label_it=entry.holiday.label_it,
                label_en=entry.holiday.label_en,
                afternoon_only=entry.holiday.afternoon_only,
            )
            for entry in holiday_service.dates_in_year(holidays, year)
        ],
    )
