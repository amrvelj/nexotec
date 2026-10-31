"""location_opening_hours + location_closure (KAN-204; Dealer Administration
PRD › Locations, D-A-27): when a site is open, and when it is closed beyond
the cantonal holidays.

**Opening hours are stored per department, always** (A-20 → D-A-27): one row
per (location, department, weekday) the site is open, Sales, Service and
Parts alike. The "same for all departments" mode of the screen — the default
— writes identical rows for the three, so the schema never changes when a
four-person garage grows into separate counters. A weekday without a row is
closed. Each day has one interval and an optional second one (the lunch
break). Times are local wall-clock times at the site (Europe/Zurich), never
instants.

**Closures** are the dealer's own Betriebsferien and one-off closed days —
from, to (both inclusive) and a reason; the cantonal holidays are not typed
here, they come from the location's `holiday_calendar`.

Both are platform-owned children of `location`, so the FKs are real.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import CheckConstraint, Date, ForeignKey, SmallInteger, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class LocationDepartment(str, enum.Enum):
    SALES = "sales"
    SERVICE = "service"
    PARTS = "parts"


class LocationOpeningHours(PrimaryKeyMixin, Base):
    """One open weekday of one department. Replaced as a set with the
    location's version (the hours PUT), so the rows carry no version of their
    own."""

    __tablename__ = "location_opening_hours"
    __table_args__ = (
        UniqueConstraint(
            "location_id", "department", "weekday", name="uq_location_opening_hours_location_department_weekday"
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_location_opening_hours_weekday"),
        CheckConstraint("opens_at < closes_at", name="ck_location_opening_hours_interval"),
        CheckConstraint(
            "(second_opens_at IS NULL AND second_closes_at IS NULL) OR "
            "(second_opens_at IS NOT NULL AND second_closes_at IS NOT NULL "
            "AND closes_at < second_opens_at AND second_opens_at < second_closes_at)",
            name="ck_location_opening_hours_second_interval",
        ),
    )

    location_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("location.id"), nullable=False, index=True)
    department: Mapped[LocationDepartment] = mapped_column(StoredEnum(LocationDepartment, length=16), nullable=False)
    # ISO 8601: 1 = Monday … 7 = Sunday.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[dt.time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[dt.time] = mapped_column(Time, nullable=False)
    second_opens_at: Mapped[dt.time | None] = mapped_column(Time, nullable=True)
    second_closes_at: Mapped[dt.time | None] = mapped_column(Time, nullable=True)


class LocationClosure(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """A closure period of one site, both days inclusive."""

    __tablename__ = "location_closure"
    __table_args__ = (CheckConstraint("starts_on <= ends_on", name="ck_location_closure_period"),)

    location_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("location.id"), nullable=False, index=True)
    starts_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
