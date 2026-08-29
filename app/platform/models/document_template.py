"""DocumentTemplate (WP-6b PR-2, ADR-044 tier 2): the per-dealership
editable boilerplate the shared document-render layer wraps around every
module's content — "invoice and contract wording" in ADR-044's own words.

One row per dealership, not one per document type — the brief describes
"shared header and footer" as a single mechanism, and Dealership itself
already carries the true letterhead facts (legal name, address, phone,
logo, brand colour — see app.platform.models.dealership). This table adds
only what doesn't already exist anywhere: the editable header-note/footer-
text boilerplate, in all four languages. A dealership with no row here yet
is not an error — app.platform.services.document_render treats a missing
template the same as one with every field empty, so a brand-new dealership
can render documents on day one before anyone has customized anything.
"""

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID
from app.db import Base


class DocumentTemplate(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "document_template"

    dealership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=False, unique=True, index=True
    )

    header_note_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_it: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    footer_text_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_text_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_text_it: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_text_en: Mapped[str | None] = mapped_column(Text, nullable=True)
