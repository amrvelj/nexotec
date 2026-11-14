"""UI translations served at runtime (Dealer Administration D-A-23, FR-A-30…32;
KAN-209). Platform-owned: the UI strings are Nexotec's, never a dealer's —
no tenant column, and no endpoint lets a dealer write one (FR-A-32).

`ui_translation` holds one row per (namespace, key, language). The namespace
is a bundle's top-level object (`shell`, `common`, `userRole`, …) and the key
the dotted path below it, so `shell` + `nav.business` is the string the SPA
asks for as `t('shell.nav.business')`. The rows come from the frontend
bundles (`scripts/import_ui_translation_bundles.py`, source `bundle_seed`);
a platform admin's edit turns a row's `source` to `platform` and keeps the
bundle text it started from in `seed_value`, which is what the panel diffs
against and what a revert restores.

Only `platform` rows are served by `GET /v1/i18n/{language}`: a `bundle_seed`
row is a copy of the shipped bundle, so serving it would add nothing while
the bundles agree, and would silently undo a developer's later bundle change
once they do not. The bundle stays the fallback for every key the platform
has not edited (FR-A-30).

`ui_translation_revision` is the one global version (FR-A-31): every publish
and revert bumps it in the same transaction, under its row lock, and the
runtime endpoint's ETag is built from it — so a cached map is invalidated by
any platform edit and by nothing else.

`missing_translation` is the log of keys a browser found in neither the table
nor its bundle and rendered as the loud marker (FR-A-30: "reported, never
rendered blank"): one row per (key, language), counted, never a row per
report.
"""

import datetime as dt
import enum

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.i18n import SwissLanguage
from app.core.types import UTCDateTime
from app.db import Base


class UiTranslationSource(str, enum.Enum):
    """Where a row's live `value` comes from (FR-A-30)."""

    BUNDLE_SEED = "bundle_seed"
    PLATFORM = "platform"


class UiTranslation(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "ui_translation"
    __table_args__ = (
        UniqueConstraint("namespace", "key", "language", name="uq_ui_translation_namespace_key_language"),
        # The runtime map reads one language's platform rows.
        Index("ix_ui_translation_language_source", "language", "source"),
    )

    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=2), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    seed_value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The bundle text this row was seeded from: the panel's diff and a revert's target.",
    )
    source: Mapped[UiTranslationSource] = mapped_column(StoredEnum(UiTranslationSource, length=16), nullable=False)


class UiTranslationRevision(Base):
    """The single row holding the global version of the served UI strings."""

    __tablename__ = "ui_translation_revision"

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class MissingTranslation(PrimaryKeyMixin, Base):
    __tablename__ = "missing_translation"
    __table_args__ = (UniqueConstraint("key", "language", name="uq_missing_translation_key_language"),)

    key: Mapped[str] = mapped_column(String(255), nullable=False, comment="The full dotted key the SPA asked for.")
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=2), nullable=False)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_reported_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_reported_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
