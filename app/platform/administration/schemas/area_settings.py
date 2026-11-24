"""Schemas of Dealer Administration's settings-store API (KAN-805 PR-2,
ADR-214, ADR-216).

A value travels in the field of its kind: `switch`, `number` (a number or an
amount, a decimal string), `choice`, or `text` (one name and its
translations, ruling 176). `null` in a write clears the slot back to «not
set»; «not set» is a state, never a default value.
"""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel


class TextValueBody(CamelModel):
    name: str = Field(max_length=500)
    # {"fr": …, "it": …, "en": …, "de": …}: only the languages translated.
    translations: dict[str, str] = Field(default_factory=dict)


class SettingValueBody(CamelModel):
    """A value: exactly the field of the setting's kind."""

    switch: bool | None = None
    number: Decimal | None = None
    choice: str | None = Field(default=None, max_length=64)
    text: TextValueBody | None = None


class SettingWrite(CamelModel):
    """The new value of a slot; `null` clears it back to «not set»."""

    value: SettingValueBody | None


class SettingDeclarationRead(CamelModel):
    key: str
    area: str
    module: str | None
    kind: str
    when_not_set: str
    acts_as: SettingValueBody | None
    minimum: Decimal | None
    maximum: Decimal | None
    decimals: int
    unit: str | None
    choices: list[str]
    checklist_item: bool
    checklist_blocks: str | None


class SettingSlotRead(CamelModel):
    """The Betrieb's slot (`locationId` null) or a location's. `version` 0: never written."""

    location_id: uuid.UUID | None
    is_set: bool
    value: SettingValueBody | None
    version: int
    changed_at: dt.datetime | None
    changed_by: uuid.UUID | None


class AreaSettingRead(CamelModel):
    declaration: SettingDeclarationRead
    dealership: SettingSlotRead
    locations: list[SettingSlotRead]


class SettingLocationRead(CamelModel):
    id: uuid.UUID
    name: str
    is_active: bool


class AreaSettingsRead(CamelModel):
    area: str
    # The per-location column: more than one active location, or any location value (Anto, 2026-11-24).
    shows_locations: bool
    locations: list[SettingLocationRead]
    settings: list[AreaSettingRead]


class SettingAreaRead(CamelModel):
    area: str
    setting_count: int


class SettingAreaList(CamelModel):
    items: list[SettingAreaRead]


class SettingChangeRead(CamelModel):
    id: uuid.UUID
    key: str
    location_id: uuid.UUID | None
    origin: str
    is_set: bool
    value: SettingValueBody | None
    version_after: int
    restored_from_id: uuid.UUID | None
    created_at: dt.datetime
    created_by: uuid.UUID | None


class SettingChangePage(CamelModel):
    items: list[SettingChangeRead]
    next_cursor: str | None = None
