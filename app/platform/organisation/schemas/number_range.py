"""KAN-207 — the Number ranges screen's API shapes."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.permissions import ModuleCode
from app.core.schemas import CamelModel
from app.platform.organisation.models.number_range import NumberRangeDocumentType


class NumberRangeRead(CamelModel):
    document_type: NumberRangeDocumentType
    module: ModuleCode
    pattern: str
    start_value: int
    yearly_reset: bool
    # The value the range stands at (read-only on screen); null before its first.
    last_value: int | None
    # The number it would hand out next — the screen's example.
    next_number: str
    # Set by the first number handed out (A-RULE-3): from then on only a
    # platform admin changes the range, with a reason.
    frozen_at: dt.datetime | None
    version: int


class NumberRangeList(CamelModel):
    items: list[NumberRangeRead]
    # Whether this caller may change a frozen range (platform staff).
    may_change_frozen: bool
    # KAN-214: when the manager last confirmed the ranges («Nummernkreise
    # bestätigen», the setup checklist's item); null = never.
    confirmed_at: dt.datetime | None = None


class NumberRangeUpdate(CamelModel):
    """A field left out stays as it is. `reason` is required to change a
    frozen range (platform admin only) and recorded in the audit row."""

    pattern: str | None = Field(default=None, min_length=1, max_length=64)
    start_value: int | None = Field(default=None, ge=1)
    yearly_reset: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


class NumberRangePreviewRequest(CamelModel):
    document_type: NumberRangeDocumentType
    pattern: str = Field(min_length=1, max_length=64)
    start_value: int = Field(ge=1)
    yearly_reset: bool


class NumberRangePreviewRead(CamelModel):
    next_number: str


class NumberRangeConfirmationRead(CamelModel):
    """KAN-214: one «Nummernkreise bestätigen»."""

    confirmed_at: dt.datetime
    confirmed_by: uuid.UUID
