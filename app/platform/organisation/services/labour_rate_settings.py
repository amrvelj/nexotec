"""The labour-rate settings (KAN-806; Aftersales «Core data model and
settings» 8.2, O16, O18, O19; rulings 177 and 183) and their readers.

- **O16** `labour_rates.rates_entered`: the Betrieb types its labour rates
  net or with VAT (ruling 177). Nexotec cannot choose it for the Betrieb, so
  «not set» refuses: no rate or agreement can be entered without it, and it is
  an item of the Aftersales setup checklist (linked to the labour-rates screen).
- **O18** `labour_rates.time_unit`: labour time is entered and shown in hours
  or in work units (AW). «Not set» acts as hours.
- **O19** `labour_rates.work_units_per_hour`: how many work units make an
  hour. O18 «work units» cannot be chosen without it, and it cannot be
  cleared while work units are in force (check 49; the store's cross-key
  rule, `setting_rules`).

Each may differ per location (ruling 175). They are declared here, where
they are read — the rate entry reads O16; `labour_time_unit` reads O18 and
O19 for Aftersales' positions (KAN-810) — and registered by the composition
root `app/setting_registry.py` through `platform.public`. The rate itself is
always per hour (ruling 183); a work unit is a way of showing time.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.money import PriceEntry
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingKind,
    WhenNotSet,
)
from app.platform.organisation.services.setting_store import read_setting

logger = logging.getLogger(__name__)

RATES_ENTERED_KEY = "labour_rates.rates_entered"
TIME_UNIT_KEY = "labour_rates.time_unit"
WORK_UNITS_PER_HOUR_KEY = "labour_rates.work_units_per_hour"

HOURS = "hours"
WORK_UNITS = "work_units"

RATES_ENTERED = SettingDeclaration(
    key=RATES_ENTERED_KEY,
    module="aftersales",
    kind=SettingKind.CHOICE,
    when_not_set=WhenNotSet.REFUSES,
    choices=(PriceEntry.NET.value, PriceEntry.GROSS.value),
    # An item of the Aftersales setup checklist (slice 1, section 5), linked to
    # /administration/settings/labour_rates — until KAN-805 PR-5's generic screen, an
    # interim route to the labour-rates screen (ADR-219 addendum).
    checklist_item=True,
)
TIME_UNIT = SettingDeclaration(
    key=TIME_UNIT_KEY,
    module="aftersales",
    kind=SettingKind.CHOICE,
    when_not_set=WhenNotSet.ACTS_AS,
    acts_as=HOURS,
    choices=(HOURS, WORK_UNITS),
    choice_requires=((WORK_UNITS, WORK_UNITS_PER_HOUR_KEY),),
)
WORK_UNITS_PER_HOUR = SettingDeclaration(
    key=WORK_UNITS_PER_HOUR_KEY,
    module="aftersales",
    kind=SettingKind.NUMBER,
    when_not_set=WhenNotSet.QUIET,
    minimum=Decimal(1),
    maximum=Decimal(100),
    unit="work_units_per_hour",
)


# Registered by the composition root `app/setting_registry.py` through
# `platform.public.register_setting` (which also judges a checklist action).
LABOUR_RATE_SETTINGS = (RATES_ENTERED, TIME_UNIT, WORK_UNITS_PER_HOUR)


def rates_entered(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None) -> PriceEntry | None:
    """O16 at this location (None: the Betrieb's own): whether rates are typed
    net or with VAT, or None while not set — then no rate may be entered."""

    read = read_setting(db, dealership_id=dealership_id, location_id=location_id, key="labour_rates.rates_entered")
    return PriceEntry(read.value) if read.is_set and isinstance(read.value, str) else None


@dataclass(frozen=True)
class LabourTimeUnit:
    """How a Betrieb enters and shows labour time at a location (ruling 183).
    `work_units_per_hour` is set when `unit` is work units, and only then."""

    unit: str
    work_units_per_hour: Decimal | None
    unit_is_set: bool


def labour_time_unit(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID | None) -> LabourTimeUnit:
    """O18 and O19 at this location: hours, or work units with how many make
    an hour. «Not set» is hours. Work units without work units per hour cannot
    be written (check 49); should a stored pair ever say so — a location
    deactivated while it relied on the Betrieb's O19, that cleared, the
    location reactivated — this answers hours and logs it, and platform's
    reconciliation reports it."""

    unit = read_setting(db, dealership_id=dealership_id, location_id=location_id, key="labour_rates.time_unit")
    if unit.effective != WORK_UNITS:
        return LabourTimeUnit(unit=HOURS, work_units_per_hour=None, unit_is_set=unit.is_set)
    per_hour = read_setting(
        db, dealership_id=dealership_id, location_id=location_id, key="labour_rates.work_units_per_hour"
    )
    if not per_hour.is_set or not isinstance(per_hour.value, Decimal):
        logger.warning(
            "Business %s (location %s) shows labour in work units without work units per hour: hours used.",
            dealership_id,
            location_id,
        )
        return LabourTimeUnit(unit=HOURS, work_units_per_hour=None, unit_is_set=True)
    return LabourTimeUnit(unit=WORK_UNITS, work_units_per_hour=per_hour.value, unit_is_set=True)
