"""The Betrieb's status page and Nexotec's incidents (KAN-223)."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.core.status import StatusState
from app.platform.models.status_page import IncidentSeverity, PlatformStatusIncident


class FourLanguageText(CamelModel):
    de: str = Field(min_length=1)
    fr: str = Field(min_length=1)
    it: str = Field(min_length=1)
    en: str = Field(min_length=1)


class StatusIncidentRead(CamelModel):
    id: uuid.UUID
    severity: IncidentSeverity
    title: FourLanguageText
    body: FourLanguageText
    starts_at: dt.datetime
    ends_at: dt.datetime | None
    version: int


class StatusItemRead(CamelModel):
    key: str = Field(description="Stable per Betrieb: `integration.connection.<id>`, `number_range.offer`, …")
    source: str = Field(description="The source that contributed it: number_ranges, integration, nexotec.")
    state: StatusState
    message_key: str = Field(description="The SPA i18n key that says what is wrong (or that it is fine).")
    params: dict[str, str | int] = Field(description="Values the message interpolates.")
    link: str | None = Field(description="The SPA path that fixes it; null when the dealer cannot.")
    incident: StatusIncidentRead | None = Field(
        default=None, description="Nexotec's incident behind a `nexotec.incident.*` item, in all four languages."
    )


class StatusPageRead(CamelModel):
    dealership_id: uuid.UUID
    state: StatusState = Field(description="The worst item's state: ok only when every item is ok.")
    attention_count: int = Field(description="How many items are warning or blocked — the header indicator's count.")
    items: list[StatusItemRead]


class StatusIncidentList(CamelModel):
    items: list[StatusIncidentRead]


class StatusIncidentCreate(CamelModel):
    severity: IncidentSeverity
    title: FourLanguageText
    body: FourLanguageText
    starts_at: dt.datetime | None = Field(default=None, description="Defaults to now.")
    ends_at: dt.datetime | None = None


class StatusIncidentUpdate(CamelModel):
    severity: IncidentSeverity | None = None
    title: FourLanguageText | None = None
    body: FourLanguageText | None = None
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = Field(
        default=None, description="Set to end the incident; send null explicitly to reopen it, omit to keep it."
    )


def incident_read(row: PlatformStatusIncident) -> StatusIncidentRead:
    return StatusIncidentRead(
        id=row.id,
        severity=row.severity,
        title=FourLanguageText(**row.title),
        body=FourLanguageText(**row.body),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        version=row.version,
    )
