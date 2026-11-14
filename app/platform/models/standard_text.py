"""StandardText — the Textbaustein library (KAN-208; Dealer Administration
PRD "Standard texts", A-RULE-4, FR-A-07/08/28/29/54, D-A-23, D-A-26).

One row per (scope, code, language) — never one row with four language
columns. Three kinds of row share the table:

- a **platform seed** (`source = platform_seed`, `dealership_id` NULL): the
  starter library Nexotec ships in DE/FR/IT/EN, edited by `platform_admin`
  only (FR-A-29, the Translations panel is KAN-209's);
- a **dealer text** at dealership scope (`dealership_id` set, `location_id`
  NULL) — either the Betrieb's own code, or an override of a seed: a dealer
  edit of a seeded text creates this row with `overridden_from_id` naming the
  seed row of the same code and language, so a later platform edit of the
  seed never overwrites the dealer's wording. `overridden_seed_version` is
  the seed version the dealer last reviewed; a seed whose version moved past
  it is "the standard text has changed, review yours" — derived on read,
  never a stored flag a job has to keep in step;
- a **location text** (`location_id` set): wins over the dealership's text
  of the same code where both exist.

`category`, `applies_to` and `slot` say where a text may be used (the
closed registry in `app.platform.services.standard_text_placeholders`);
several texts may share one (applies_to, slot) and one of them is the
default the module prints unless the user picks another. A WhatsApp text
carries the provider's template approval (FR-A-54). All referenced tables
are platform's own, so the foreign keys are real.
"""

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.i18n import SwissLanguage
from app.core.types import GUID
from app.db import Base
from app.platform.identity.models.standard_text_delegation import StandardTextCategory


class StandardTextAppliesTo(str, enum.Enum):
    """The document and message types a text may be used on (PRD
    `applies_to`; FR-A-52's registered types the starter set needs)."""

    OFFER = "offer"
    CONTRACT = "contract"
    INVOICE = "invoice"
    APPOINTMENT_REMINDER = "appointment_reminder"
    VEHICLE_READY = "vehicle_ready"
    WORKSHOP_ORDER = "workshop_order"
    CUSTOMER_NOTE = "customer_note"


class StandardTextSlot(str, enum.Enum):
    """Where on the document or message the text sits (PRD)."""

    HEADER = "header"
    INTRO = "intro"
    BODY = "body"
    FOOTER = "footer"
    TERMS = "terms"
    CLOSING = "closing"


class StandardTextScope(str, enum.Enum):
    DEALERSHIP = "dealership"
    LOCATION = "location"


class StandardTextSource(str, enum.Enum):
    PLATFORM_SEED = "platform_seed"
    DEALER = "dealer"


class StandardText(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "standard_text"
    __table_args__ = (
        # One row per code and language at each scope. Three partial indexes
        # because a NULL dealership / location never collides in a plain
        # unique constraint.
        Index(
            "uq_standard_text_seed_code_language",
            "code",
            "language",
            unique=True,
            postgresql_where=text("dealership_id IS NULL"),
            sqlite_where=text("dealership_id IS NULL"),
        ),
        Index(
            "uq_standard_text_dealership_code_language",
            "dealership_id",
            "code",
            "language",
            unique=True,
            postgresql_where=text("dealership_id IS NOT NULL AND location_id IS NULL"),
            sqlite_where=text("dealership_id IS NOT NULL AND location_id IS NULL"),
        ),
        Index(
            "uq_standard_text_location_code_language",
            "location_id",
            "code",
            "language",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
            sqlite_where=text("location_id IS NOT NULL"),
        ),
        # A seed has no dealership and is never an override; a dealer row has
        # one. Location scope exactly when a location is named.
        CheckConstraint(
            "(source = 'platform_seed' AND dealership_id IS NULL AND location_id IS NULL "
            "AND overridden_from_id IS NULL) OR (source = 'dealer' AND dealership_id IS NOT NULL)",
            name="ck_standard_text_source_owner",
        ),
        CheckConstraint(
            "(scope = 'location') = (location_id IS NOT NULL)",
            name="ck_standard_text_scope_location",
        ),
    )

    # NULL = a platform seed (Nexotec's), else the Betrieb that owns the text.
    dealership_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=True, index=True
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("location.id"), nullable=True)
    scope: Mapped[StandardTextScope] = mapped_column(StoredEnum(StandardTextScope, length=16), nullable=False)

    code: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[StandardTextCategory] = mapped_column(StoredEnum(StandardTextCategory, length=16), nullable=False)
    applies_to: Mapped[StandardTextAppliesTo] = mapped_column(
        StoredEnum(StandardTextAppliesTo, length=32), nullable=False
    )
    slot: Mapped[StandardTextSlot] = mapped_column(StoredEnum(StandardTextSlot, length=16), nullable=False)
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=8), nullable=False)

    # The name the user picks the text by; never what a module resolves by.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[StandardTextSource] = mapped_column(StoredEnum(StandardTextSource, length=16), nullable=False)
    overridden_from_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("standard_text.id"), nullable=True, index=True
    )
    overridden_seed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # FR-A-54: a WhatsApp text outside the 24-hour window must be a
    # provider-approved template; meaningful on category = whatsapp only.
    template_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_template_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
