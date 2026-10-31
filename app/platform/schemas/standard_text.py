"""Standard texts over the API (KAN-208)."""

import datetime as dt
import uuid

from pydantic import Field, model_validator

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.platform.models.standard_text import (
    StandardTextAppliesTo,
    StandardTextScope,
    StandardTextSlot,
    StandardTextSource,
)
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.schemas.patch import refuse_null_on_not_null_fields

_BODY_MAX = 20_000


class StandardTextRead(CamelModel):
    id: uuid.UUID
    dealership_id: uuid.UUID | None
    location_id: uuid.UUID | None
    scope: StandardTextScope
    code: str
    category: StandardTextCategory
    applies_to: StandardTextAppliesTo
    slot: StandardTextSlot
    language: SwissLanguage
    title: str
    body: str
    source: StandardTextSource
    overridden_from_id: uuid.UUID | None
    # FR-A-29: the seed this override was adapted from changed since the
    # Betrieb last reviewed it — "the standard text has changed, review yours".
    seed_changed: bool
    is_default: bool
    active: bool
    sort_order: int
    template_approved: bool
    provider_template_id: str | None
    version: int
    updated_at: dt.datetime


class StandardTextList(CamelModel):
    items: list[StandardTextRead]


class StandardTextCreate(CamelModel):
    """A new text of the Betrieb (`code` omitted: the server names it), a
    further language or location variant of one of its texts, or the
    adaptation of a seed (`code` = the seed's). The category is the path's."""

    code: str | None = Field(default=None, min_length=1, max_length=100)
    language: SwissLanguage
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=_BODY_MAX)
    applies_to: StandardTextAppliesTo | None = None
    slot: StandardTextSlot | None = None
    location_id: uuid.UUID | None = None
    is_default: bool | None = None
    active: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    template_approved: bool = False
    provider_template_id: str | None = Field(default=None, max_length=200)


class StandardTextUpdate(CamelModel):
    """PATCH semantics: a field not sent keeps its value. `seedReviewed:
    true` records that the seed's current wording was reviewed (an
    adaptation only)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=_BODY_MAX)
    is_default: bool | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    template_approved: bool | None = None
    provider_template_id: str | None = Field(default=None, max_length=200)
    seed_reviewed: bool | None = None

    @model_validator(mode="after")
    def _not_null_fields_are_not_cleared(self) -> "StandardTextUpdate":
        refuse_null_on_not_null_fields(self, nullable={"provider_template_id", "seed_reviewed"})
        return self


class StandardTextPreviewRequest(CamelModel):
    category: StandardTextCategory
    applies_to: StandardTextAppliesTo
    language: SwissLanguage
    body: str = Field(max_length=_BODY_MAX)
    location_id: uuid.UUID | None = None


class StandardTextPreviewRead(CamelModel):
    text: str


class StandardTextTargetRead(CamelModel):
    category: StandardTextCategory
    applies_to: StandardTextAppliesTo
    slots: list[StandardTextSlot]
    placeholders: list[str]


class StandardTextRegistryRead(CamelModel):
    """The closed placeholder set and where each text may be used — what
    the editor offers (FR-A-07)."""

    placeholders: list[str]
    targets: list[StandardTextTargetRead]
