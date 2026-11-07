"""The provider caption store (KAN-547, ADR-111 (3)): written by «Test
connection» (`gateway.test_connection`), read through `integration.public`.

`store_captions` upserts one language of one provider's captions: a code the
provider still sends gets this language's label (and `fetched_at`); a code it
no longer sends is left as it is — a vocabulary never shrinks under a car
that was read with it. No tenant, no user: captions are global vocabulary.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.adapters.base import CaptionData
from app.integration.models.caption import IntegrationProviderCaption

CAPTION_LANGUAGES: tuple[str, ...] = ("de", "fr", "it", "en")
_LABEL_COLUMN = {lang: f"label_{lang}" for lang in CAPTION_LANGUAGES}


@dataclasses.dataclass(frozen=True)
class ProviderCaption:
    """Plain data for another context: the provider's wording of one code in
    the four languages (a language not fetched yet is `None`)."""

    provider: str
    caption_group: str
    kind: str
    code: str
    label_de: str | None
    label_fr: str | None
    label_it: str | None
    label_en: str | None
    label_short_de: str | None
    fetched_at: dt.datetime


def store_captions(
    db: Session, *, provider_code: str, lang: str, captions: Iterable[CaptionData], now: dt.datetime | None = None
) -> int:
    """Upserts one language. Returns how many rows were written or touched.
    Flushes; the caller owns the transaction."""

    if lang not in _LABEL_COLUMN:
        raise ValueError(f"Unsupported caption language {lang!r}; one of {CAPTION_LANGUAGES}.")
    column = _LABEL_COLUMN[lang]
    fetched_at = now or utcnow()
    existing = {
        (row.caption_group, row.kind, row.code): row
        for row in db.scalars(
            select(IntegrationProviderCaption).where(IntegrationProviderCaption.provider == provider_code)
        )
    }
    written = 0
    seen: set[tuple[str, str, str]] = set()
    for caption in captions:
        key = (caption.group, caption.kind, caption.code)
        if key in seen:
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = IntegrationProviderCaption(
                provider=provider_code, caption_group=caption.group, kind=caption.kind, code=caption.code,
                fetched_at=fetched_at,
            )
            db.add(row)
            existing[key] = row
        setattr(row, column, caption.label)
        if lang == "de":
            row.label_short_de = caption.label_short
        row.fetched_at = fetched_at
        written += 1
    db.flush()
    return written


def _to_data(row: IntegrationProviderCaption) -> ProviderCaption:
    return ProviderCaption(
        provider=row.provider, caption_group=row.caption_group, kind=row.kind, code=row.code,
        label_de=row.label_de, label_fr=row.label_fr, label_it=row.label_it, label_en=row.label_en,
        label_short_de=row.label_short_de, fetched_at=row.fetched_at,
    )


def get_captions(
    db: Session, *, provider_code: str, caption_group: str, codes: Sequence[str] | None = None
) -> list[ProviderCaption]:
    """Every caption of one group of one provider (optionally only the given
    codes), every list (`kind`) included — the reader picks the list that
    matches its vehicle kind, `global` applying to all."""

    stmt = select(IntegrationProviderCaption).where(
        IntegrationProviderCaption.provider == provider_code,
        IntegrationProviderCaption.caption_group == caption_group,
    )
    if codes is not None:
        stmt = stmt.where(IntegrationProviderCaption.code.in_(list(codes)))
    stmt = stmt.order_by(
        IntegrationProviderCaption.kind, IntegrationProviderCaption.code, IntegrationProviderCaption.id
    )
    return [_to_data(row) for row in db.scalars(stmt)]


def caption_languages_fetched(db: Session, *, provider_code: str) -> dict[str, dt.datetime | None]:
    """Per language, when it was last fetched (None: never) — what a staff
    screen shows to judge whether a vocabulary is stale."""

    result: dict[str, dt.datetime | None] = {}
    for lang, column in _LABEL_COLUMN.items():
        result[lang] = db.scalar(
            select(func.max(IntegrationProviderCaption.fetched_at)).where(
                IntegrationProviderCaption.provider == provider_code,
                getattr(IntegrationProviderCaption, column).is_not(None),
            )
        )
    return result
