"""UI translations (KAN-209, FR-A-30…32): the runtime map, the platform panel's
listing and edits, and the missing-key report."""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import Field

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.platform.models.ui_translation import UiTranslationSource

# A dotted i18next key path the SPA can ask for: letters, digits, `_` and `-`
# segments. Anything else is not a key and is refused, never stored.
KEY_PATH_PATTERN = r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$"
MAX_VALUE_LENGTH = 4000
MAX_MISSING_KEYS_PER_REPORT = 50


class UiTranslationMap(CamelModel):
    """`GET /v1/i18n/{language}`: what the platform has published for that
    language, as `"<namespace>.<key>": value`. Every key absent here is the
    shipped bundle's (FR-A-30)."""

    language: SwissLanguage
    version: int = Field(description="The global UI-translation version this map was read at; the ETag carries it.")
    values: dict[str, str]


class UiTranslationRead(CamelModel):
    id: uuid.UUID
    namespace: str
    key: str
    language: SwissLanguage
    value: str
    seed_value: str
    source: UiTranslationSource
    version: int
    updated_at: dt.datetime
    updated_by: uuid.UUID | None


class UiTranslationKeyRead(CamelModel):
    """One key with its row per language — the panel's four columns. A
    language the seed never had is null."""

    namespace: str
    key: str
    de: UiTranslationRead | None = None
    fr: UiTranslationRead | None = None
    it: UiTranslationRead | None = None
    en: UiTranslationRead | None = None


class UiTranslationKeyPage(CamelModel):
    items: list[UiTranslationKeyRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool


class UiTranslationNamespaces(CamelModel):
    items: list[str]


class UiTranslationPublish(CamelModel):
    """The value a platform admin publishes for one key in one language."""

    value: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)


class MissingTranslationReport(CamelModel):
    """Keys the browser rendered as the loud marker in `language`."""

    language: SwissLanguage
    keys: list[Annotated[str, Field(max_length=255, pattern=KEY_PATH_PATTERN)]] = Field(
        min_length=1, max_length=MAX_MISSING_KEYS_PER_REPORT
    )


class MissingTranslationReportResult(CamelModel):
    recorded: int


class MissingTranslationRead(CamelModel):
    key: str
    language: SwissLanguage
    report_count: int
    first_reported_at: dt.datetime
    last_reported_at: dt.datetime


class MissingTranslationPage(CamelModel):
    items: list[MissingTranslationRead]
