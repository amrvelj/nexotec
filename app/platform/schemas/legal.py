"""Legal documents and their acceptance (KAN-214): the published AGB and
privacy notice, a manager's acceptance, and what is still due. Split out of
`schemas/setup.py` (KAN-635 PR 3b) so identity can take it: sign-in reads
what is due."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.i18n import SwissLanguage
from app.core.schemas import CamelModel
from app.platform.models.legal import DEFAULT_ACCEPTANCE_GRACE_DAYS, LegalDocument, LegalDocumentType
from app.platform.services import legal_documents


class LegalDocumentSummary(CamelModel):
    id: uuid.UUID
    document_type: LegalDocumentType
    version: str
    language: SwissLanguage
    effective_date: dt.date
    title: str
    draft: bool = Field(description="Below version 1.0 — a draft for counsel (production does not activate on it).")


class LegalDocumentRead(LegalDocumentSummary):
    body_markdown: str
    acceptance_grace_days: int
    published_at: dt.datetime


class LegalAcceptanceDue(CamelModel):
    """The current versions the Betrieb has not accepted (FR-A-39)."""

    documents: list[LegalDocumentSummary]
    due_by: dt.date | None = Field(description="The last day of grace; after it the managers are held.")
    held: bool = Field(description="The grace has passed: a manager reaches only the acceptance screen.")


class LegalAcceptanceRequest(CamelModel):
    legal_document_id: uuid.UUID


class LegalAcceptanceRead(CamelModel):
    id: uuid.UUID
    legal_document_id: uuid.UUID
    document_type: LegalDocumentType
    version: str
    language: SwissLanguage
    user_id: uuid.UUID
    accepted_at: dt.datetime


class LegalAcceptanceList(CamelModel):
    items: list[LegalAcceptanceRead]


class CurrentLegalDocuments(CamelModel):
    items: list[LegalDocumentRead]


class LegalDocumentList(CamelModel):
    items: list[LegalDocumentRead]


class LegalDocumentPublish(CamelModel):
    document_type: LegalDocumentType
    version: str = Field(min_length=1, max_length=16)
    language: SwissLanguage
    effective_date: dt.date
    title: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=200_000)
    acceptance_grace_days: int = Field(default=DEFAULT_ACCEPTANCE_GRACE_DAYS, ge=0, le=365)


def document_summary(row: LegalDocument) -> LegalDocumentSummary:
    return LegalDocumentSummary(
        id=row.id,
        document_type=row.document_type,
        version=row.version,
        language=row.language,
        effective_date=row.effective_date,
        title=row.title,
        draft=legal_documents.is_draft(row.version),
    )


def document_read(row: LegalDocument) -> LegalDocumentRead:
    return LegalDocumentRead(
        **document_summary(row).model_dump(),
        body_markdown=row.body_markdown,
        acceptance_grace_days=row.acceptance_grace_days,
        published_at=row.published_at,
    )


def acceptance_due(state: legal_documents.AcceptanceState, language: str | None) -> LegalAcceptanceDue:
    return LegalAcceptanceDue(
        documents=[document_summary(current.in_language(language)) for current in state.due],
        due_by=state.due_by,
        held=state.held,
    )
