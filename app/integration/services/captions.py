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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.adapters.base import CaptionData
from app.integration.models.caption import IntegrationProviderCaption, IntegrationProviderCaptionFetch

CAPTION_LANGUAGES: tuple[str, ...] = ("de", "fr", "it", "en")
_LABEL_COLUMN = {lang: f"label_{lang}" for lang in CAPTION_LANGUAGES}
# The columns' widths; the identAPI sets no length on a caption, so a longer
# one is cut rather than refused (a label, not a key).
_LABEL_WIDTH = 200
_SHORT_WIDTH = 80


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
    Flushes; the caller owns the transaction. Two Betriebe may test their
    connections at once on this shared table: a row another transaction
    inserted meanwhile is taken over in a savepoint, never a 500."""

    if lang not in _LABEL_COLUMN:
        raise ValueError(f"Unsupported caption language {lang!r}; one of {CAPTION_LANGUAGES}.")
    column = _LABEL_COLUMN[lang]
    fetched_at = now or utcnow()
    written = 0
    seen: set[tuple[str, str, str]] = set()
    for caption in captions:
        key = (caption.group, caption.kind, caption.code)
        if key in seen:
            continue
        seen.add(key)
        row = _find(db, provider_code, caption)
        if row is None:
            row = IntegrationProviderCaption(
                provider=provider_code, caption_group=caption.group, kind=caption.kind, code=caption.code,
                fetched_at=fetched_at,
            )
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:
                # The concurrent winner's row: take it over.
                db.expire_all()
                row = _find(db, provider_code, caption)
                if row is None:  # pragma: no cover - the constraint that just fired names this row
                    raise
        label = (caption.label or "")[:_LABEL_WIDTH] or None
        setattr(row, column, label)
        if lang == "de":
            row.label_short_de = (caption.label_short or "")[:_SHORT_WIDTH] or None
        row.fetched_at = fetched_at
        written += 1
    _record_fetch(db, provider_code=provider_code, lang=lang, fetched_at=fetched_at, count=written)
    db.flush()
    return written


def _record_fetch(db: Session, *, provider_code: str, lang: str, fetched_at: dt.datetime, count: int) -> None:
    """KAN-588: this language of this provider was fetched now, with `count`
    codes — upserted in a savepoint, as a caption row is, so two Betriebe
    testing at once never 500."""

    row = db.scalar(
        select(IntegrationProviderCaptionFetch).where(
            IntegrationProviderCaptionFetch.provider == provider_code, IntegrationProviderCaptionFetch.lang == lang
        )
    )
    if row is None:
        row = IntegrationProviderCaptionFetch(provider=provider_code, lang=lang, fetched_at=fetched_at, caption_count=count)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return
        except IntegrityError:
            db.expire_all()
            row = db.scalar(
                select(IntegrationProviderCaptionFetch).where(
                    IntegrationProviderCaptionFetch.provider == provider_code,
                    IntegrationProviderCaptionFetch.lang == lang,
                )
            )
            if row is None:  # pragma: no cover - the constraint that just fired names this row
                raise
    row.fetched_at = fetched_at
    row.caption_count = count


@dataclasses.dataclass(frozen=True)
class CaptionFetch:
    """KAN-588 — when one language of one provider's captions was last
    fetched, and how many codes came back."""

    provider: str
    lang: str
    fetched_at: dt.datetime
    caption_count: int


def list_caption_fetches(db: Session) -> list[CaptionFetch]:
    """Every (provider, language) fetched so far, by provider then in the
    order of CAPTION_LANGUAGES."""

    order = {lang: index for index, lang in enumerate(CAPTION_LANGUAGES)}
    rows = db.scalars(select(IntegrationProviderCaptionFetch)).all()
    return sorted(
        (CaptionFetch(provider=r.provider, lang=r.lang, fetched_at=r.fetched_at, caption_count=r.caption_count) for r in rows),
        key=lambda f: (f.provider, order.get(f.lang, len(order))),
    )


def _find(db: Session, provider_code: str, caption: CaptionData) -> IntegrationProviderCaption | None:
    return db.scalar(
        select(IntegrationProviderCaption).where(
            IntegrationProviderCaption.provider == provider_code,
            IntegrationProviderCaption.caption_group == caption.group,
            IntegrationProviderCaption.kind == caption.kind,
            IntegrationProviderCaption.code == caption.code,
        )
    )


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
