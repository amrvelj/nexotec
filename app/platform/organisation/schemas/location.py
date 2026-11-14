"""Locations of a Betrieb (KAN-204; Dealer Administration PRD › Locations,
D-A-02, D-A-27, FR-A-03, A-RULE-1), their opening hours and closures, and the
cantonal holiday calendars a location picks from.

The address is flat, like the columns, and judged as a block by the service
(`services/location.py`): a PATCH may carry one field of it, and a row from
before KAN-204 may hold a locality alone.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BeforeValidator, EmailStr, Field, field_validator, model_validator

from app.core.schemas import CamelModel
from app.core.validators import E164Phone
from app.platform.organisation.models.dealership import LocationType
from app.platform.organisation.models.holiday_calendar import HolidayCondition, HolidayRule
from app.platform.organisation.models.location_schedule import LocationDepartment
from app.platform.reference.schemas.patch import refuse_null_on_not_null_fields

# "ZH-NORD": capitals, digits and hyphens, starting with a letter or digit.
LOCATION_CODE_PATTERN = r"^[A-Z0-9][A-Z0-9-]{0,19}$"


def _upper_code(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


# Upper-cased before the pattern is judged: "zh-nord" saves as "ZH-NORD".
LocationCode = Annotated[str, BeforeValidator(_upper_code)]


def _unique_types(types: list[LocationType] | None) -> list[LocationType] | None:
    if types is not None and len(set(types)) != len(types):
        raise ValueError("locationTypes lists the same type twice.")
    return types


class _LocationFields(CamelModel):
    """The fields create and PATCH share. `geoLat`/`geoLng` are WGS 84
    degrees; both or neither (the service judges the pair after a PATCH)."""

    location_types: list[LocationType] | None = None
    address_street: str | None = Field(default=None, max_length=200)
    address_house_number: str | None = Field(default=None, max_length=20)
    address_postal_code: str | None = Field(default=None, max_length=12)
    address_locality: str | None = Field(default=None, max_length=100)
    address_canton: str | None = Field(default=None, max_length=2)
    address_country: str | None = Field(default=None, max_length=2)
    contact_person: str | None = Field(default=None, max_length=200)
    phone: E164Phone | None = None
    email: EmailStr | None = Field(default=None, max_length=254)
    lead_email: EmailStr | None = Field(default=None, max_length=254)
    geo_lat: float | None = Field(default=None, ge=-90, le=90)
    geo_lng: float | None = Field(default=None, ge=-180, le=180)
    holiday_calendar_id: uuid.UUID | None = None
    letterhead_address_override: str | None = Field(default=None, max_length=500)
    bank_account_override_id: uuid.UUID | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None

    @field_validator("location_types")
    @classmethod
    def _types_unique(cls, value: list[LocationType] | None) -> list[LocationType] | None:
        return _unique_types(value)


class LocationCreate(_LocationFields):
    """A new site. `code` is the dealer's short key, fixed once saved;
    without `holidayCalendarId` the canton's own calendar is taken where the
    canton has one (a canton whose holidays differ by region has none — the
    dealer picks the region)."""

    code: LocationCode = Field(pattern=LOCATION_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    location_types: list[LocationType] = Field(default_factory=list)


class LocationUpdate(_LocationFields):
    """PATCH semantics: a field not sent keeps its value, null clears a
    nullable one. `code` may be set once, on a location saved without one;
    after that it is refused (422 `location_code_immutable`). `isActive:
    false` deactivates — never a delete (A-RULE-1) — and is refused for the
    Betrieb's last active location (409 `last_active_location`, FR-A-03)."""

    code: LocationCode | None = Field(default=None, pattern=LOCATION_CODE_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "LocationUpdate":
        refuse_null_on_not_null_fields(
            self,
            nullable={
                "address_street",
                "address_house_number",
                "address_postal_code",
                "address_locality",
                "address_canton",
                "address_country",
                "contact_person",
                "phone",
                "email",
                "lead_email",
                "geo_lat",
                "geo_lng",
                "holiday_calendar_id",
                "letterhead_address_override",
                "bank_account_override_id",
                "valid_from",
                "valid_to",
            },
        )
        return self


class LocationRead(CamelModel):
    """A site of the Betrieb. `bankAccountOverrideId` is filled only for the
    roles that read banking (manager, finance, auditor, platform staff —
    ADR-100); for anyone else it is null, as if no override existed.
    `calendarRef` and `warehouseRef` are Aftersales' and Parts' to fill;
    read-only here."""

    id: uuid.UUID
    dealership_id: uuid.UUID
    code: str | None
    name: str
    location_types: list[LocationType]
    address_street: str | None
    address_house_number: str | None
    address_postal_code: str | None
    address_locality: str | None
    address_canton: str | None
    address_country: str | None
    contact_person: str | None
    phone: str | None
    email: str | None
    lead_email: str | None
    geo_lat: float | None
    geo_lng: float | None
    holiday_calendar_id: uuid.UUID | None
    letterhead_address_override: str | None
    bank_account_override_id: uuid.UUID | None
    is_active: bool
    valid_from: dt.date | None
    valid_to: dt.date | None
    calendar_ref: str | None
    warehouse_ref: str | None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class LocationList(CamelModel):
    items: list[LocationRead]


# --- Opening hours ------------------------------------------------------------


def _minute(value: dt.time | None) -> dt.time | None:
    if value is not None and (value.second or value.microsecond or value.tzinfo):
        raise ValueError("opening hours are local times to the minute (HH:MM).")
    return value


class OpeningHoursDay(CamelModel):
    """One open weekday (ISO: 1 = Monday … 7 = Sunday) — one interval and an
    optional second one, e.g. 08:00–12:00 and 13:30–18:00. `department` is
    sent only when the hours differ per department."""

    department: LocationDepartment | None = None
    weekday: int = Field(ge=1, le=7)
    opens_at: dt.time
    closes_at: dt.time
    second_opens_at: dt.time | None = None
    second_closes_at: dt.time | None = None

    @field_validator("opens_at", "closes_at", "second_opens_at", "second_closes_at")
    @classmethod
    def _to_the_minute(cls, value: dt.time | None) -> dt.time | None:
        return _minute(value)

    @model_validator(mode="after")
    def _intervals_in_order(self) -> "OpeningHoursDay":
        if self.opens_at >= self.closes_at:
            raise ValueError("closesAt must be after opensAt.")
        if (self.second_opens_at is None) != (self.second_closes_at is None):
            raise ValueError("the second interval needs both secondOpensAt and secondClosesAt.")
        if self.second_opens_at is not None and self.second_closes_at is not None:
            if self.second_opens_at <= self.closes_at:
                raise ValueError("the second interval must start after the first one closes.")
            if self.second_opens_at >= self.second_closes_at:
                raise ValueError("secondClosesAt must be after secondOpensAt.")
        return self


class OpeningHoursUpdate(CamelModel):
    """The site's whole week, replacing what is stored (PUT, If-Match on the
    location's version). `perDepartment: false` — the default mode, one set
    for the whole site — sends days without a department and the service
    writes each for Sales, Service and Parts alike (D-A-27: stored per
    department always). `perDepartment: true` sends a department on every
    day. A weekday that is not sent is closed."""

    per_department: bool
    hours: list[OpeningHoursDay] = Field(max_length=21)

    @model_validator(mode="after")
    def _departments_match_the_mode(self) -> "OpeningHoursUpdate":
        seen: set[tuple[LocationDepartment | None, int]] = set()
        for day in self.hours:
            if self.per_department and day.department is None:
                raise ValueError("every day needs its department when the hours differ per department.")
            if not self.per_department and day.department is not None:
                raise ValueError("leave department out when one set of hours applies to the whole site.")
            key = (day.department, day.weekday)
            if key in seen:
                raise ValueError("a weekday is listed twice for the same department.")
            seen.add(key)
        return self


class OpeningHoursRow(CamelModel):
    department: LocationDepartment
    weekday: int
    opens_at: dt.time
    closes_at: dt.time
    second_opens_at: dt.time | None
    second_closes_at: dt.time | None


class OpeningHoursRead(CamelModel):
    """Every stored row. `perDepartment` is derived: false when the three
    departments hold the same week (what the one-set mode writes) — the
    screen opens in that mode."""

    location_id: uuid.UUID
    location_version: int
    per_department: bool
    hours: list[OpeningHoursRow]


# --- Closures -------------------------------------------------------------------


class ClosureCreate(CamelModel):
    """A closure of the whole site (Betriebsferien, a one-off closed day),
    both days inclusive."""

    starts_on: dt.date
    ends_on: dt.date
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _in_order(self) -> "ClosureCreate":
        if self.ends_on < self.starts_on:
            raise ValueError("endsOn must not lie before startsOn.")
        return self


class ClosureUpdate(CamelModel):
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "ClosureUpdate":
        refuse_null_on_not_null_fields(self)
        return self


class ClosureRead(CamelModel):
    id: uuid.UUID
    location_id: uuid.UUID
    starts_on: dt.date
    ends_on: dt.date
    reason: str
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class ClosureList(CamelModel):
    items: list[ClosureRead]


# --- Holiday calendars (platform reference data) ---------------------------------


class HolidayRead(CamelModel):
    """A holiday as its rule: `fixed_date` (month, day), `easter_offset`
    (days from Easter Sunday) or a named rule. `condition` drops it in a year
    the canton does not observe it; `afternoonOnly` is Solothurn's Labour Day."""

    code: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    rule: HolidayRule
    month: int | None
    day: int | None
    easter_offset_days: int | None
    condition: HolidayCondition
    afternoon_only: bool


class HolidayCalendarRead(CamelModel):
    id: uuid.UUID
    code: str
    canton: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    source: str
    holidays: list[HolidayRead]


class HolidayCalendarList(CamelModel):
    items: list[HolidayCalendarRead]


class HolidayDateRead(CamelModel):
    date: dt.date
    code: str
    label_de: str
    label_fr: str
    label_it: str
    label_en: str
    afternoon_only: bool


class HolidayDateList(CamelModel):
    calendar_id: uuid.UUID
    year: int
    items: list[HolidayDateRead]
