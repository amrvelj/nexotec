"""DocumentTemplate schemas (WP-6b PR-2)."""

import datetime as dt
import uuid

from app.core.schemas import CamelModel


class DocumentTemplateUpdate(CamelModel):
    """All fields optional — PATCH semantics, partial update. A field left
    unset is left unchanged; a field explicitly set to null clears it back
    to "not customized yet".
    """

    header_note_de: str | None = None
    header_note_fr: str | None = None
    header_note_it: str | None = None
    header_note_en: str | None = None
    footer_text_de: str | None = None
    footer_text_fr: str | None = None
    footer_text_it: str | None = None
    footer_text_en: str | None = None


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
    machinery needs no special-casing for "doesn't exist yet" at all.
    """

    id: uuid.UUID | None
    dealership_id: uuid.UUID
    header_note_de: str | None
    header_note_fr: str | None
    header_note_it: str | None
    header_note_en: str | None
    footer_text_de: str | None
    footer_text_fr: str | None
    footer_text_it: str | None
    footer_text_en: str | None
    version: int
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
