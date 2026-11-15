"""Calendar boundaries in the session time zone (Europe/Zurich), resolved on
the server — the grids' relative date conditions («heute», «diese Woche», «in
den letzten 30 Tagen», U-09, KAN-474) and the absolute ones («am», «vor»,
«nach» a date, KAN-461). Moved here from `app/customer/services/customer_search.py`
(KAN-461) so the shared list-query layer and Customers resolve a day the
same way. Cross-cutting: imports no bounded context."""

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

from app.core.config import get_settings

CalendarPeriodCode = Literal["day", "week", "month", "year"]


def session_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().session_timezone)


def today_in_session_zone(now: dt.datetime | None = None) -> tuple[dt.date, ZoneInfo]:
    zone = session_zone()
    return (now or dt.datetime.now(dt.UTC)).astimezone(zone).date(), zone


def start_of_day(day: dt.date) -> dt.datetime:
    """Midnight of `day` in the session time zone."""

    return dt.datetime.combine(day, dt.time.min, tzinfo=session_zone())


def start_of_day_days_ago(days: int, now: dt.datetime | None = None) -> dt.datetime:
    """Midnight, in the session time zone, of the calendar day `days` before
    today there. `now` pins the clock for a test."""

    today, zone = today_in_session_zone(now)
    return dt.datetime.combine(today - dt.timedelta(days=days), dt.time.min, tzinfo=zone)


def start_of_current_period(period: CalendarPeriodCode, now: dt.datetime | None = None) -> dt.datetime:
    """Midnight, in the session time zone, at which the current calendar day,
    week (Monday, ISO), month or year began there. `now` pins the clock for
    a test."""

    today, zone = today_in_session_zone(now)
    if period == "day":
        start = today
    elif period == "week":
        start = today - dt.timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        start = today.replace(month=1, day=1)
    return dt.datetime.combine(start, dt.time.min, tzinfo=zone)
