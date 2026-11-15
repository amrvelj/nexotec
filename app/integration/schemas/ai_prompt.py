"""The AI prompt library and the AI connection in use, over the API (KAN-227)."""

import datetime as dt
import uuid

from pydantic import Field, model_validator

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.integration.models.ai_prompt import AiPromptSource
from app.integration.models.connection import ConnectionStatus

_PROMPT_MAX = 20_000


class AiPromptRead(CamelModel):
    id: uuid.UUID
    code: str
    language: SwissLanguage
    title: str
    system_prompt: str
    user_prompt: str
    source: AiPromptSource
    overridden_from_id: uuid.UUID | None
    # «The default prompt has changed, review yours» — derived on read.
    seed_changed: bool
    active: bool
    version: int
    updated_at: dt.datetime


class AiPromptCodeRead(CamelModel):
    code: str
    placeholders: list[str]


class AiConnectionInUseRead(CamelModel):
    """The Betrieb's AI connection generation uses (`services/ai_generation.py`)."""

    connection_id: uuid.UUID
    display_name: str
    provider_code: str
    provider_name: str
    status: ConnectionStatus


class AiPromptList(CamelModel):
    """Every seed and every override of the Betrieb; the codes with their
    placeholders; the connection in use (null: none connected)."""

    items: list[AiPromptRead]
    codes: list[AiPromptCodeRead]
    connection: AiConnectionInUseRead | None


class AiPromptCreate(CamelModel):
    """The Betrieb's adaptation of a seed (same code and language)."""

    code: str = Field(min_length=1, max_length=64)
    language: SwissLanguage
    system_prompt: str = Field(min_length=1, max_length=_PROMPT_MAX)
    user_prompt: str = Field(min_length=1, max_length=_PROMPT_MAX)


def _refuse_nulls(body: CamelModel, *, nullable: set[str]) -> None:
    cleared = [
        field.alias or name
        for name, field in type(body).model_fields.items()
        if name not in nullable and name in body.model_fields_set and getattr(body, name) is None
    ]
    if cleared:
        raise ValueError(f"{', '.join(cleared)} cannot be null — leave the field out to keep its value.")


class AiPromptUpdate(CamelModel):
    """PATCH: a field not sent keeps its value. `seedReviewed: true` records
    that the seed's current wording was reviewed."""

    system_prompt: str | None = Field(default=None, min_length=1, max_length=_PROMPT_MAX)
    user_prompt: str | None = Field(default=None, min_length=1, max_length=_PROMPT_MAX)
    active: bool | None = None
    seed_reviewed: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> "AiPromptUpdate":
        _refuse_nulls(self, nullable={"seed_reviewed"})
        return self


class AiPromptSeedRead(CamelModel):
    id: uuid.UUID
    language: SwissLanguage
    title: str
    system_prompt: str
    user_prompt: str
    # What Nexotec ships, to diff against and revert to (null: not shipped).
    shipped_title: str | None
    shipped_system_prompt: str | None
    shipped_user_prompt: str | None
    edited: bool
    version: int
    updated_at: dt.datetime
    updated_by: uuid.UUID | None


class AiPromptSeedGroup(CamelModel):
    code: str
    placeholders: list[str]
    de: AiPromptSeedRead | None = None
    fr: AiPromptSeedRead | None = None
    it: AiPromptSeedRead | None = None
    en: AiPromptSeedRead | None = None


class AiPromptSeedList(CamelModel):
    items: list[AiPromptSeedGroup]


class AiPromptSeedUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=_PROMPT_MAX)
    user_prompt: str | None = Field(default=None, min_length=1, max_length=_PROMPT_MAX)

    @model_validator(mode="after")
    def _not_null(self) -> "AiPromptSeedUpdate":
        _refuse_nulls(self, nullable=set())
        return self
