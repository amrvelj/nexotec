"""The generic "content definition" vocabulary (WP-6b PR-3) — what a module
supplies to app.platform.services.document_render.render_document(). No
field anywhere is named after a real document type, a price breakdown, VAT,
or a vehicle: those stay entirely with whatever module builds one of these
later (WP-7/8/9). This is the direct generalization of the prototype's own
renderer (src/19-document.js) — a dealer/letterhead block and a footer are
supplied by the render layer itself from Dealership/Location/
DocumentTemplate data, never by the caller; everything below is what fills
the page between them.

Every user-visible string here (labels, paragraph text, headings) is
CONTENT and must already be in the caller's chosen correspondence
language — this schema carries no vocabulary of its own to translate.
"""

import enum
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.core.schemas import CamelModel


class LineStyle(str, enum.Enum):
    """The prototype's four visual weights for a line-items row
    (src/02-base.css's .doc-line/.sub/.total/.grand) — NORMAL is a plain
    row, SUB is an indented sub-line (e.g. an individual option under a
    base price), TOTAL is a running-total row (bold, ruled), GRAND is the
    final payable amount (larger, double-ruled).
    """

    NORMAL = "normal"
    SUB = "sub"
    TOTAL = "total"
    GRAND = "grand"


class KeyValueRow(CamelModel):
    label: str
    value: str


class DocumentLine(CamelModel):
    label: str
    # A raw amount, not a pre-formatted string — document_render.py owns
    # calling app.core.i18n.format_currency_chf, so every document this
    # layer ever renders gets the same Swiss formatting (and the same
    # apostrophe-normalization fix) with no chance of a caller doing it
    # slightly differently.
    amount: Decimal
    style: LineStyle = LineStyle.NORMAL


class KeyValueBlock(CamelModel):
    kind: Literal["key_value"] = "key_value"
    heading: str | None = None
    rows: list[KeyValueRow]
    # True for a bordered card (the prototype's .doc-veh box); False for a
    # plain list (the prototype's .doc-meta block).
    boxed: bool = False


class ParagraphBlock(CamelModel):
    kind: Literal["paragraph"] = "paragraph"
    text: str


class LineItemsBlock(CamelModel):
    kind: Literal["line_items"] = "line_items"
    lines: list[DocumentLine]


class SignatureBlock(CamelModel):
    """A row of blank signature lines, each with a caller-supplied,
    already-translated label underneath (e.g. "Buyer"/"Seller") — the
    prototype's .doc-sign block, generalized to any number of signatories
    rather than assuming exactly two.
    """

    kind: Literal["signature"] = "signature"
    labels: list[str]
    # KAN-206 (Anto, 2026-11-12, Q2 A): whose signature image prints above
    # the line of the same position — a platform user id or None (a line to
    # sign by hand). The image itself is read at render time, never frozen
    # here, so a reprint shows the user's current image (stationery is
    # current); a user without one prints the bare line.
    signer_user_ids: list[uuid.UUID | None] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_signer_per_line_at_most(self) -> "SignatureBlock":
        if len(self.signer_user_ids) > len(self.labels):
            raise ValueError("signerUserIds names more signers than the block has lines.")
        return self


DocumentBlock = Annotated[
    KeyValueBlock | ParagraphBlock | LineItemsBlock | SignatureBlock,
    Field(discriminator="kind"),
]


class DocumentWatermark(str, enum.Enum):
    """KAN-206: the word printed across every page — DRAFT («ENTWURF» /
    «PROJET» / «BOZZA» / «DRAFT») on a draft document, while the Betrieb's
    template has it on; SAMPLE («MUSTER») on the template preview, always.
    A render argument, never part of a frozen content definition: whether a
    document is still a draft is decided when it is printed."""

    DRAFT = "draft"
    SAMPLE = "sample"


class Addressee(CamelModel):
    """The recipient block (the prototype's .doc-to) — already-formatted
    lines (name, street, postal code + locality, …), in whatever order the
    caller's own address-formatting convention produces.
    """

    lines: list[str]


class ContentDefinition(CamelModel):
    title: str
    # The prototype's top-right .doc-meta block (date, valid-until,
    # advisor, …) — a plain key-value list, distinct from a KeyValueBlock
    # in the body so it always renders in that fixed position.
    metadata: list[KeyValueRow] | None = None
    addressee: Addressee | None = None
    blocks: list[DocumentBlock] = Field(default_factory=list)
    # KAN-208: the document's footer standard text (e.g. `offer.footer.*`),
    # already resolved and rendered by the caller through
    # app.platform.public.render_standard_text and frozen with the document;
    # the render layer prints it in the running footer above the address
    # line. None (and every document generated before KAN-208) prints none.
    footer_text: str | None = None
