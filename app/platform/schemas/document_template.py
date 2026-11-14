"""DocumentTemplate schemas (WP-6b PR-2; KAN-206 the letterhead layout)."""

import datetime as dt
import uuid

from pydantic import ConfigDict, Field, field_validator

from app.core.schemas import CamelModel
from app.platform.models.document_template import AddressWindow, FooterColumn, HeaderLayout, LogoSize

MAX_FOOTER_COLUMNS = 4


def _valid_footer_columns(value: list[FooterColumn] | None) -> list[FooterColumn] | None:
    """At most four, none twice, and the company column always among them:
    A-27 — the legal footer always prints the legal entity."""

    if value is None:
        return None
    if len(set(value)) != len(value):
        raise ValueError("footerColumns lists the same column twice.")
    if FooterColumn.COMPANY not in value:
        raise ValueError("footerColumns must include 'company' — the footer always prints the legal entity (A-27).")
    return value


class DocumentTemplateUpdate(CamelModel):
    """The header note in four languages — KAN-208 moved the footer text
    into the standard-text library (`invoice.footer.carried_over`) — and,
    since KAN-206, the letterhead layout. All fields optional — PATCH
    semantics, partial update. A field left unset is left unchanged; a
    header note explicitly set to null clears it back to "not customized
    yet"; a layout field is NOT NULL and refuses null. A field this schema
    does not know — a stale client's `footerText*` among them — is a 422,
    never silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    header_note_de: str | None = None
    header_note_fr: str | None = None
    header_note_it: str | None = None
    header_note_en: str | None = None
    header_layout: HeaderLayout | None = None
    logo_size: LogoSize | None = None
    address_window: AddressWindow | None = None
    footer_columns: list[FooterColumn] | None = Field(default=None, min_length=1, max_length=MAX_FOOTER_COLUMNS)
    draft_watermark: bool | None = None
    page_numbers: bool | None = None

    @field_validator("footer_columns")
    @classmethod
    def _footer_columns(cls, value: list[FooterColumn] | None) -> list[FooterColumn] | None:
        return _valid_footer_columns(value)

    @field_validator("header_layout", "logo_size", "address_window", "footer_columns", "draft_watermark", "page_numbers")
    @classmethod
    def _layout_is_not_cleared(cls, value: object) -> object:
        # A null for one of these is a 422, never "back to the default":
        # the column is NOT NULL, and the default is a value to send.
        if value is None:
            raise ValueError("This field cannot be cleared; send one of its values.")
        return value


class DocumentTemplateRead(CamelModel):
    """Returned even when no row exists yet for the dealership — id/
    timestamps are null in that case
    (app.platform.services.document_template.get_document_template_or_default),
    matching the "absent, not 404" convention
    app.platform.api.user_preferences already established for "nothing
    customized here yet". `version` is `0` (never null) in that case rather
    than the usual VersionedMixin start of `1` — a real, ordinary sentinel
    the client reads off GET and echoes back as `If-Match: 0` to create the
    row on the first PATCH, so the shared require_if_match/check_version
    machinery needs no special-casing for "doesn't exist yet" at all. The
    layout fields read the shipped defaults until the first save.
    """

    id: uuid.UUID | None
    dealership_id: uuid.UUID
    header_note_de: str | None
    header_note_fr: str | None
    header_note_it: str | None
    header_note_en: str | None
    header_layout: HeaderLayout
    logo_size: LogoSize
    address_window: AddressWindow
    footer_columns: list[FooterColumn]
    draft_watermark: bool
    page_numbers: bool
    version: int
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
