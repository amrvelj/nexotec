"""DocumentTemplate (WP-6b PR-2, ADR-044 tier 2): the per-dealership
editable boilerplate the shared document-render layer wraps around every
module's content — "invoice and contract wording" in ADR-044's own words.

One row per dealership, not one per document type — the brief describes
"shared header and footer" as a single mechanism, and Dealership itself
already carries the true letterhead facts (legal name, address, phone,
logo, brand colour — see app.platform.models.dealership). This table adds
only what doesn't already exist anywhere: the editable header-note/footer-
text boilerplate, in all four languages. Since KAN-208 only the header note
is left here; document wording lives in the standard-text library
(app.platform.models.standard_text). A dealership with no row here yet
is not an error — app.platform.services.document_render treats a missing
template the same as one with every field empty, so a brand-new dealership
can render documents on day one before anyone has customized anything.

KAN-206 (PRD "Letterhead and the document template"; Anto, 2026-11-12) adds
the layout of the one shared template: where the logo sits and how large it
prints, which side the C5/C4 envelope window is on (left or right per
Betrieb, default right — Q1 B), which master-data columns the footer shows
and in what order, whether a draft prints «ENTWURF» and whether pages are
numbered. Every one is NOT NULL with a server default, so an existing row and
the "no row yet" default read the same. What the footer columns print is
never stored here: it is read from the Betrieb's master data at render time.
"""

import enum
import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Text, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID
from app.db import Base


class HeaderLayout(str, enum.Enum):
    """Where the logo sits in the letterhead; the sender block takes the
    other side (centred: the logo above the sender line)."""

    LOGO_LEFT = "logo_left"
    LOGO_RIGHT = "logo_right"
    LOGO_CENTRED = "logo_centred"


class LogoSize(str, enum.Enum):
    """The logo's maximum printed width — 30 / 40 / 50 mm — at 20 mm high at
    most (Anto, 2026-11-12, as recommended)."""

    S = "s"
    M = "m"
    L = "l"


class AddressWindow(str, enum.Enum):
    """The side of the C5/C4 envelope window the addressee prints for."""

    LEFT = "left"
    RIGHT = "right"


class FooterColumn(str, enum.Enum):
    """The footer's master-data blocks (PRD: up to four columns)."""

    COMPANY = "company"
    CONTACT = "contact"
    BANKING = "banking"
    REGISTER = "register"


DEFAULT_FOOTER_COLUMNS: tuple[FooterColumn, ...] = (
    FooterColumn.COMPANY,
    FooterColumn.CONTACT,
    FooterColumn.BANKING,
    FooterColumn.REGISTER,
)


class DocumentTemplate(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "document_template"
    # The migrated shape (eb38e2956aee): a unique constraint under Postgres's
    # default name plus a plain index — declared as such so the models and
    # the migrations agree (KAN-170).
    __table_args__ = (UniqueConstraint("dealership_id", name="document_template_dealership_id_key"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=False, index=True
    )

    header_note_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_it: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_note_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # KAN-206 — the letterhead layout. The server defaults are the shipped
    # defaults; the migration gives every existing row the same.
    header_layout: Mapped[HeaderLayout] = mapped_column(
        StoredEnum(HeaderLayout, length=16),
        nullable=False,
        default=HeaderLayout.LOGO_LEFT,
        server_default=HeaderLayout.LOGO_LEFT.value,
    )
    logo_size: Mapped[LogoSize] = mapped_column(
        StoredEnum(LogoSize, length=1), nullable=False, default=LogoSize.M, server_default=LogoSize.M.value
    )
    address_window: Mapped[AddressWindow] = mapped_column(
        StoredEnum(AddressWindow, length=8),
        nullable=False,
        default=AddressWindow.RIGHT,
        server_default=AddressWindow.RIGHT.value,
    )
    # Ordered codes of FooterColumn, at most four, `company` always among
    # them (A-27: the legal footer always prints the legal entity).
    footer_columns: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: [column.value for column in DEFAULT_FOOTER_COLUMNS],
        server_default=text("""'["company", "contact", "banking", "register"]'"""),
    )
    draft_watermark: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    page_numbers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    # The footer_text_* columns moved into the standard-text library as
    # `invoice.footer.carried_over` (KAN-208): a footer is a standard text,
    # never a column scattered across tables (PRD "Standard texts").
