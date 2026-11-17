"""IntegrationAiPrompt — the prompt library of the Betrieb's own AI provider
(KAN-227; Dealer Administration PRD D-A-33, FR-A-36/38).

The pattern is the standard texts' (KAN-208, `app/platform/documents/models/standard_text.py`),
on purpose — the admin panel must not grow a second kind of override:

- a **seed** (`source = platform_seed`, `tenant_id` NULL): the prompt Nexotec
  ships, per code and language, edited by `platform_admin` only through the
  Translations panel (D-A-23);
- a **dealer prompt** (`source = dealer`, `tenant_id` set): the Betrieb's
  override of a seed, `overridden_from_id` naming the seed row of the same
  code and language. A later edit of the seed never overwrites it;
  `overridden_seed_version` is the seed version the Betrieb last reviewed, and
  a seed whose version moved past it is "the default prompt has changed,
  review yours" — **derived on read**, never a stored flag.

Two differences from the standard texts, both because a prompt is not a
printed text: there is no location scope (a listing text is the Betrieb's,
whichever site sells the car) and no Betrieb-own codes — the codes are the
closed registry in `services/ai_prompt_registry.py` (`vehicle_listing` in v1,
D-A-33 names one use case), each the input of one feature that calls
`integration.public.generate`.

The table is integration's (ADR "AI prompts and generation live in
integration"): the prompt is half of the provider request the gateway
composes, and the provider connection is integration's. `tenant_id` is the
platform context's `Dealership.id` as a plain id (rule 2), as on every
integration row.
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


class AiPromptSource(str, enum.Enum):
    PLATFORM_SEED = "platform_seed"
    DEALER = "dealer"


class IntegrationAiPrompt(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "integration_ai_prompt"
    __table_args__ = (
        # One seed per code and language, one override per Betrieb, code and
        # language. Partial indexes: a NULL tenant never collides in a plain
        # unique constraint.
        Index(
            "uq_integration_ai_prompt_seed_code_language",
            "code",
            "language",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_integration_ai_prompt_tenant_code_language",
            "tenant_id",
            "code",
            "language",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        # A seed has no Betrieb and overrides nothing; a dealer prompt is
        # always an override of a seed (there are no Betrieb-own codes).
        CheckConstraint(
            "(source = 'platform_seed' AND tenant_id IS NULL AND overridden_from_id IS NULL) "
            "OR (source = 'dealer' AND tenant_id IS NOT NULL AND overridden_from_id IS NOT NULL)",
            name="ck_integration_ai_prompt_source_owner",
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
        comment="Owned by the platform context (Dealership.id). No DB-level FK. NULL = a Nexotec seed.",
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=8), nullable=False)
    # The name the editor shows; never what generation resolves by.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The provider's system message (the instructions) and the user message
    # (the template the caller's variables fill, `{{ token }}`).
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[AiPromptSource] = mapped_column(StoredEnum(AiPromptSource, length=16), nullable=False)
    overridden_from_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("integration_ai_prompt.id"), nullable=True, index=True
    )
    overridden_seed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
