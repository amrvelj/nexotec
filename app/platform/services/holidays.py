"""Cantonal holiday calendars (KAN-204): reading the platform reference data
and turning a calendar's rules into the dates of a year.

A holiday is stored as a rule (models/holiday_calendar.py), so the same row
answers for every year: a fixed day, an offset from Easter Sunday (the
Gregorian computus — the Anonymous Gregorian algorithm, valid for every
Gregorian year), or one of three named rules. Conditions drop a date the
canton does not observe that year (Neuchâtel's Monday-only days, Appenzell's
St Stephen's Day). A holiday that falls on a Sunday is still listed — it is
a holiday, the site is simply closed anyway.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.platform.models.holiday_calendar import Holiday, HolidayCalendar, HolidayCondition, HolidayRule

MIN_YEAR = 1900
MAX_YEAR = 2200

_MONDAY, _TUESDAY, _THURSDAY, _SATURDAY, _SUNDAY = 0, 1, 3, 5, 6


def easter_sunday(year: int) -> dt.date:
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _naefelser_fahrt(year: int) -> dt.date:
    """Glarus: the first Thursday of April, one week later when it falls in
    Holy Week (Palm Sunday to Holy Saturday)."""

    thursday = _nth_weekday(year, 4, _THURSDAY, 1)
    easter = easter_sunday(year)
    if easter - dt.timedelta(days=7) <= thursday < easter:
        return thursday + dt.timedelta(days=7)
    return thursday


def _jeune_genevois(year: int) -> dt.date:
    """Geneva: the Thursday after the first Sunday of September."""

    return _nth_weekday(year, 9, _SUNDAY, 1) + dt.timedelta(days=4)


def _federal_fast_monday(year: int) -> dt.date:
    """Vaud: the Monday after the Federal Fast (the third Sunday of September)."""

    return _nth_weekday(year, 9, _SUNDAY, 3) + dt.timedelta(days=1)


def rule_date(holiday: Holiday, year: int) -> dt.date:
    match holiday.rule:
        case HolidayRule.FIXED_DATE:
            assert holiday.month is not None and holiday.day is not None  # ck_holiday_fixed_date_has_day
            return dt.date(year, holiday.month, holiday.day)
        case HolidayRule.EASTER_OFFSET:
            assert holiday.easter_offset_days is not None  # ck_holiday_easter_offset_has_days
            return easter_sunday(year) + dt.timedelta(days=holiday.easter_offset_days)
        case HolidayRule.NAEFELSER_FAHRT:
            return _naefelser_fahrt(year)
        case HolidayRule.JEUNE_GENEVOIS:
            return _jeune_genevois(year)
        case HolidayRule.FEDERAL_FAST_MONDAY:
            return _federal_fast_monday(year)
    raise AssertionError(f"unknown holiday rule {holiday.rule!r}")


def observed(holiday: Holiday, date: dt.date) -> bool:
    match holiday.condition:
        case HolidayCondition.ALWAYS:
            return True
        case HolidayCondition.ONLY_ON_MONDAY:
            return date.weekday() == _MONDAY
        case HolidayCondition.NOT_ON_TUESDAY_OR_SATURDAY:
            return date.weekday() not in (_TUESDAY, _SATURDAY)
    raise AssertionError(f"unknown holiday condition {holiday.condition!r}")


@dataclass(frozen=True)
class HolidayDate:
    date: dt.date
    holiday: Holiday


def dates_in_year(holidays: list[Holiday], year: int) -> list[HolidayDate]:
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise UnprocessableEntityError(
            f"year must lie between {MIN_YEAR} and {MAX_YEAR}.", details={"reason": "year_out_of_range"}
        )
    out = []
    for holiday in holidays:
        date = rule_date(holiday, year)
        if observed(holiday, date):
            out.append(HolidayDate(date=date, holiday=holiday))
    return sorted(out, key=lambda d: (d.date, d.holiday.sort_order))


def list_calendars(db: Session) -> list[HolidayCalendar]:
    return list(
        db.scalars(
            select(HolidayCalendar)
            .where(HolidayCalendar.active.is_(True))
            .order_by(HolidayCalendar.sort_order, HolidayCalendar.code)
        ).all()
    )


def holidays_by_calendar(db: Session, calendar_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Holiday]]:
    out: dict[uuid.UUID, list[Holiday]] = {cid: [] for cid in calendar_ids}
    if not calendar_ids:
        return out
    rows = db.scalars(
        select(Holiday).where(Holiday.calendar_id.in_(calendar_ids)).order_by(Holiday.calendar_id, Holiday.sort_order)
    ).all()
    for row in rows:
        out[row.calendar_id].append(row)
    return out


def get_calendar_or_404(db: Session, calendar_id: uuid.UUID) -> HolidayCalendar:
    calendar = db.get(HolidayCalendar, calendar_id)
    if calendar is None:
        raise NotFoundError(f"Holiday calendar {calendar_id} was not found.")
    return calendar


def get_active_calendar_or_422(db: Session, calendar_id: uuid.UUID) -> HolidayCalendar:
    """For a location write: an unknown or retired calendar is the request's
    fault (422), never a 404 of the location route."""

    calendar = db.get(HolidayCalendar, calendar_id)
    if calendar is None or not calendar.active:
        raise UnprocessableEntityError(
            "holidayCalendarId names no holiday calendar.", details={"reason": "unknown_holiday_calendar"}
        )
    return calendar


def default_calendar_for_canton(db: Session, canton: str | None) -> HolidayCalendar | None:
    """The calendar a new location in this canton starts with: the one whose
    code is the canton code. A canton whose holidays differ by region (AG,
    FR) has none — the dealer picks the region."""

    if not canton:
        return None
    return db.scalar(
        select(HolidayCalendar).where(HolidayCalendar.code == canton, HolidayCalendar.active.is_(True))
    )
