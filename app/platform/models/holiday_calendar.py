"""holiday_calendar + holiday (KAN-204; Dealer Administration PRD › Locations,
`holiday_calendar` · `closure_periods`, D-A-02): the cantonal public holidays
as platform reference data — seeded and maintained by Nexotec, never typed by
a dealer. A location picks its calendar; the dealer adds only his own
Betriebsferien (`location_closure`).

Global, not tenant-scoped (the `reference_list` reasoning): the holidays of a
canton are the same fact for every Betrieb in it.

**What a calendar holds.** The cantonal holidays that are equal to Sundays
under the Labour Act (Art. 20a para. 1 ArG) plus the federal 1 August, as
SECO publishes them ("Feiertage Schweiz", Anhang, Stand am 12.12.2024) — the
days on which a Betrieb may not employ staff without a Sunday-work permit.
`source` names the publication on every row. Where SECO lists sub-cantonal
differences (Aargau by district, Fribourg by the commune's confession,
Neuchâtel's Le Landeron, Solothurn's Bucheggberg) each variant is its own
calendar, so a location in such a region still gets exactly its days; a
canton without variants has one calendar whose `code` is the canton code,
and that one is the default for a new location in the canton.

**A holiday is a rule, not a date**, so a calendar answers for any year
(`services/holidays.py` computes it): a fixed day of the year, an offset from
Easter Sunday (Gregorian computus), or one of three named rules (the Näfelser
Fahrt, the Jeûne genevois, the Lundi du Jeûne fédéral). `condition` holds the
two published restrictions — Neuchâtel's 2 January and 26 December count only
on a Monday; Appenzell's St Stephen's Day not on a Tuesday or a Saturday —
and `afternoon_only` Solothurn's Labour Day afternoon.

Changing the seeded rows is a new migration (they are reference data with a
source, not configuration).
"""

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class HolidayRule(str, enum.Enum):
    FIXED_DATE = "fixed_date"  # `month` + `day`
    EASTER_OFFSET = "easter_offset"  # `easter_offset_days` from Easter Sunday
    # Glarus: the first Thursday of April, one week later when that Thursday
    # falls in Holy Week (the Glarus government's rule).
    NAEFELSER_FAHRT = "naefelser_fahrt"
    # Geneva: the Thursday after the first Sunday of September (RSG J 1 45,
    # note to art. 1 al. 1 let. g).
    JEUNE_GENEVOIS = "jeune_genevois"
    # Vaud: the Monday after the Federal Fast, the third Sunday of September.
    FEDERAL_FAST_MONDAY = "federal_fast_monday"


class HolidayCondition(str, enum.Enum):
    ALWAYS = "always"
    # Neuchâtel (SECO: "Le 2 janvier et le Saint-Etienne seulement s'ils
    # tombent un lundi").
    ONLY_ON_MONDAY = "only_on_monday"
    # Appenzell I.Rh. (not when three rest days would follow each other — the
    # Standeskommission's reading: a 26 December on a Saturday or a Tuesday)
    # and Appenzell A.Rh. (not when Christmas falls on a Monday or a Friday —
    # the same two days).
    NOT_ON_TUESDAY_OR_SATURDAY = "not_on_tuesday_or_saturday"


class HolidayCalendar(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "holiday_calendar"
    __table_args__ = (UniqueConstraint("code", name="uq_holiday_calendar_code"),)

    # The canton code for a canton's one calendar ("ZH"), the canton code
    # plus the region for a variant ("AG-BADEN").
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    canton: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    label_de: Mapped[str] = mapped_column(String(300), nullable=False)
    label_fr: Mapped[str] = mapped_column(String(300), nullable=False)
    label_it: Mapped[str] = mapped_column(String(300), nullable=False)
    label_en: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Holiday(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "holiday"
    __table_args__ = (
        UniqueConstraint("calendar_id", "code", name="uq_holiday_calendar_id_code"),
        CheckConstraint(
            "(rule <> 'fixed_date') OR (month BETWEEN 1 AND 12 AND day BETWEEN 1 AND 31)",
            name="ck_holiday_fixed_date_has_day",
        ),
        CheckConstraint(
            "(rule <> 'easter_offset') OR (easter_offset_days IS NOT NULL)",
            name="ck_holiday_easter_offset_has_days",
        ),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("holiday_calendar.id"), nullable=False, index=True
    )
    # Stable across calendars: "christmas", "good_friday", "naefelser_fahrt".
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    label_de: Mapped[str] = mapped_column(String(200), nullable=False)
    label_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    label_it: Mapped[str] = mapped_column(String(200), nullable=False)
    label_en: Mapped[str] = mapped_column(String(200), nullable=False)
    rule: Mapped[HolidayRule] = mapped_column(StoredEnum(HolidayRule, length=32), nullable=False)
    month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    easter_offset_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    condition: Mapped[HolidayCondition] = mapped_column(
        StoredEnum(HolidayCondition, length=32), nullable=False, default=HolidayCondition.ALWAYS
    )
    afternoon_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
