"""Legal documents and their acceptance (KAN-214; Dealer Administration
FR-A-39, D-A-34; Anto, 2026-11-13: Q1 A, Q3 A).

`legal_document` is platform-owned: Nexotec's Terms of Service (`tos`) and
Data Processing Agreement (`dpa`), one row per type × version × language.
A version is published by platform staff and never edited afterwards — a
correction is a new version. The version string is dotted numbers ("0.9",
"1.0", "1.2"); the current version of a type is the highest one whose
`effective_date` has come (`services/legal_documents.py`). The bodies are
content (markdown), never code: the 0.9 «Entwurf» drafts are seeded by the
migration that created the table, counsel's 1.0 is published through the
platform screen (Q3 A).

`acceptance_grace_days` is the platform-set grace period of FR-A-39: once a
version is effective, a Betrieb that has not accepted it is prompted at every
login of its managers for that many days; after it, only the manager is held
at the acceptance screen, colleagues keep working (Q1 A: 30 days).

`legal_acceptance` records who accepted which version for which Betrieb,
when, from which address and browser. **Append-only**: no service updates or
deletes a row, and the ORM refuses it (`_refuse_change` below) — the record
is the evidence of the legal basis for support access (A-1).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.i18n import SwissLanguage
from app.core.types import GUID, UTCDateTime
from app.db import Base

DEFAULT_ACCEPTANCE_GRACE_DAYS = 30


class LegalDocumentType(str, enum.Enum):
    TOS = "tos"
    DPA = "dpa"


class LegalDocument(PrimaryKeyMixin, Base):
    __tablename__ = "legal_document"
    __table_args__ = (
        UniqueConstraint(
            "document_type", "version", "language", name="uq_legal_document_document_type_version_language"
        ),
    )

    document_type: Mapped[LegalDocumentType] = mapped_column(StoredEnum(LegalDocumentType, length=8), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=8), nullable=False)
    effective_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_ACCEPTANCE_GRACE_DAYS)
    published_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="The staff user who published it (platform); NULL for a migration seed."
    )


class LegalAcceptance(PrimaryKeyMixin, Base):
    __tablename__ = "legal_acceptance"
    __table_args__ = (Index("ix_legal_acceptance_dealership_id_document_type", "dealership_id", "document_type"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    legal_document_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("legal_document.id"), nullable=False)
    # Denormalised from the document so the question "has this Betrieb
    # accepted version V of the DPA" needs no join and survives a reader
    # that only knows the type and the version.
    document_type: Mapped[LegalDocumentType] = mapped_column(StoredEnum(LegalDocumentType, length=8), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[SwissLanguage] = mapped_column(StoredEnum(SwissLanguage, length=8), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, comment="The accepting manager (platform user).")
    accepted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class LegalAcceptanceIsAppendOnly(RuntimeError):
    """Raised by the ORM on any UPDATE or DELETE of a `legal_acceptance` row."""


@event.listens_for(LegalAcceptance, "before_update")
@event.listens_for(LegalAcceptance, "before_delete")
def _refuse_change(_mapper, _connection, target: LegalAcceptance) -> None:
    raise LegalAcceptanceIsAppendOnly(f"legal_acceptance {target.id} is append-only.")
