"""The planner's settings (KAN-808; Aftersales «Core data model and settings»
8.3/8.4, O6 and O7; Anto, 2026-11-25) and their readers.

- **O6** `planning.grid_minutes`: the planner's grid. Work bookings and
  drop-off and pick-up slots start and end on it; blocks are free of it. A
  whole number of minutes from 5 to 60 that divides 60 (5, 6, 10, 12, 15,
  20, 30, 60), so every hour starts on the grid — the store refuses any other
  value (`SettingDeclaration.divides`), and a stored value that no longer
  fits reads as «not set».
- **O7** `planning.slot_minutes`: the one length of a drop-off and of a
  pick-up slot, 5 to 480 minutes, and a multiple of O6 wherever both are in
  force (Anto, 2026-11-25): the store refuses writing either to break it
  (`SettingDeclaration.multiple_of`, `setting_rules`), so a slot starting on
  the grid also ends on it.

Nexotec cannot choose either for the Betrieb, so «not set» refuses what
needs it (409 `planning_setting_not_set`, naming the setting): no work or
slot can be booked without O6, no slot without O7. Both are items of the
Aftersales setup checklist, linked to the generic settings screen
(/administration/settings/planning). Each may differ per location (ruling
175: the location of the thing — here the location of the booked
resources). A block reads neither, so a block stays always possible.

Declared here, where they are read, and registered by the composition root
`app/setting_registry.py` through `planning.public.register_settings`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.platform.public import SettingDeclaration, SettingKind, WhenNotSet, read_setting, register_setting

GRID_MINUTES_KEY = "planning.grid_minutes"
SLOT_MINUTES_KEY = "planning.slot_minutes"

GRID_MINUTES = SettingDeclaration(
    key=GRID_MINUTES_KEY,
    module="aftersales",
    kind=SettingKind.NUMBER,
    when_not_set=WhenNotSet.REFUSES,
    minimum=Decimal(5),
    maximum=Decimal(60),
    divides=Decimal(60),
    unit="minutes",
    checklist_item=True,
)
SLOT_MINUTES = SettingDeclaration(
    key=SLOT_MINUTES_KEY,
    module="aftersales",
    kind=SettingKind.NUMBER,
    when_not_set=WhenNotSet.REFUSES,
    minimum=Decimal(5),
    maximum=Decimal(480),
    # A slot starts and ends on the grid: O7 is a multiple of O6 (Anto, 2026-11-25).
    multiple_of=GRID_MINUTES_KEY,
    unit="minutes",
    checklist_item=True,
)

PLANNER_SETTINGS = (GRID_MINUTES, SLOT_MINUTES)


def register_settings() -> None:
    for declaration in PLANNER_SETTINGS:
        register_setting(declaration)


def _not_set(key: str) -> ConflictError:
    return ConflictError(
        f"The planner's setting {key} is not set for this business.",
        details={"reason": "planning_setting_not_set", "setting": key},
    )


def _minutes(value: object, key: str) -> int:
    if not isinstance(value, Decimal):
        raise _not_set(key)
    return int(value)


def read_minutes_or_none(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID, key: str) -> int | None:
    """O6 or O7 at this location (its own value, else the Betrieb's), or None
    while it is not set — for a read that shows the gap instead of refusing
    (the planner page, KAN-809)."""

    read = read_setting(db, dealership_id=dealership_id, location_id=location_id, key=key)
    return _minutes(read.value, key) if read.is_set else None


def grid_minutes(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> int:
    """O6 at this location (its own value, else the Betrieb's); 409 while not set."""

    read = read_setting(db, dealership_id=dealership_id, location_id=location_id, key="planning.grid_minutes")
    if not read.is_set:
        raise _not_set(GRID_MINUTES_KEY)
    return _minutes(read.value, GRID_MINUTES_KEY)


def slot_minutes(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> int:
    """O7 at this location (its own value, else the Betrieb's); 409 while not set."""

    read = read_setting(db, dealership_id=dealership_id, location_id=location_id, key="planning.slot_minutes")
    if not read.is_set:
        raise _not_set(SLOT_MINUTES_KEY)
    return _minutes(read.value, SLOT_MINUTES_KEY)
