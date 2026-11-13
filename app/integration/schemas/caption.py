"""KAN-588 — the caption vocabulary's freshness, for platform staff."""

import datetime as dt

from app.core.schemas import CamelModel


class CaptionFetchRead(CamelModel):
    provider: str
    provider_name: str
    language: str
    fetched_at: dt.datetime
    caption_count: int


class CaptionFetchList(CamelModel):
    items: list[CaptionFetchRead]
