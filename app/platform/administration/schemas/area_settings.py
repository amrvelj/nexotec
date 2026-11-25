"""Schemas of Dealer Administration's settings-store API (KAN-805 PR-2,
ADR-214, ADR-216).

A value travels in the field of its kind: `switch` (a JSON boolean), `number`
(a number or an amount, as a decimal string — never a JSON number, which would
pass through a float), `choice`, or `text` (one name and its translations,
ruling 176). The setting's declaration judges the value and answers with a
reason; the schema checks only the JSON types. `null` in a write clears the slot back to «not
set»; «not set» is a state, never a default value.
"""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field, StrictBool, StrictStr

from app.core.schemas import CamelModel


class TextValueBody(CamelModel):
    name: StrictStr
    # {"fr": …, "it": …, "en": …, "de": …}: only the languages translated.
    translations: dict[str, str] = Field(default_factory=dict)


class SettingValueBody(CamelModel):
    """A value: exactly the field of the setting's kind."""

    switch: StrictBool | None = None
    # A decimal string ("15", "4.50"); the declaration checks its decimals and bounds.
    number: StrictStr | None = Field(
        default=None,
        description="A number or an amount as a plain decimal string: ASCII digits, an optional point and "
        "decimals, an optional leading minus (\"12.5\", \"-3\"); never a JSON number, which passes through a float.",
    )
    choice: StrictStr | None = None
    text: TextValueBody | None = None


class SettingWrite(CamelModel):
    """The new value of a slot; `null` clears it back to «not set»."""

    value: SettingValueBody | None


class ChoiceRequirementRead(CamelModel):
    """Choosing `choice` needs the setting `key` in force (KAN-806, check 49)."""

    choice: str
    key: str


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
    choice_requires: list[ChoiceRequirementRead]


class SettingSlotRead(CamelModel):
    """The Betrieb's slot (`locationId` null) or a location's. `version` 0: never written."""

    location_id: uuid.UUID | None
    is_set: bool
    value: SettingValueBody | None
    version: int
    changed_at: dt.datetime | None
    changed_by: uuid.UUID | None
    # False: the value stored no longer fits the setting's declaration and is
    # read as not set — the manager sets it again.
    fits_declaration: bool


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
    # The changing user's name, resolved on read; null for a user this
    # Betrieb does not know (platform staff, a deleted user).
    created_by_name: str | None = None
    # The slot before this change: a first change follows «not set».
    is_set_before: bool = False
    value_before: SettingValueBody | None = None


class SettingChangePage(CamelModel):
    items: list[SettingChangeRead]
    next_cursor: str | None = None
