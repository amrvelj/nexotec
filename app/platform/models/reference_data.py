"""ReferenceList / ReferenceValue: admin-managed business taxonomy (Swiss
addendum Round 2 Q3) — value_code strings other entities point to instead of
a DB enum, so a new taxonomy value doesn't require a deploy.

Global, not TenantScopedMixin: this data isn't owned by a Dealership (it's the
shared vocabulary the whole platform's Vehicle/Customer/Transaction records
draw from), same "tenant-agnostic" reasoning as the Vehicle profile (Swiss
addendum decision #9). ReferenceList rows are seed-only for v1 (created by
migration, not a POST endpoint) — the API only manages ReferenceValue rows
within an existing list_code. Lifecycle/status enums are explicitly excluded
from this pattern (Round 2 Q3) and stay hard-coded on their own models.

label_en (WP-1, Gap Analysis G-18): the original Swiss addendum ruling
(Round 2 Q3) shipped DE/FR/IT only. Target Architecture's i18n rule is
DE/FR/IT/EN, the frontend already ships en.json, and the provider-
independence story depends on owning the English labels — so every
reference value renders untranslated in English until this lands. New
reference values must carry an English label going forward (Gap Analysis,
"Rules in force" #6).
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
from app.db import Base


class ReferenceList(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reference_list"

    list_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)


class ReferenceValue(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "reference_value"
    __table_args__ = (UniqueConstraint("list_id", "value_code", name="uq_reference_value_list_id_value_code"),)

    list_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("reference_list.id"), nullable=False, index=True)
    value_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label_de: Mapped[str] = mapped_column(String(200), nullable=False)
    label_fr: Mapped[str] = mapped_column(String(200), nullable=False)
    label_it: Mapped[str] = mapped_column(String(200), nullable=False)
    label_en: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    list: Mapped[ReferenceList] = relationship()

    @property
    def list_code(self) -> str:
        """API-facing convenience — the API is scoped by list_code, not the
        internal list_id FK.
        """

        return self.list.list_code
