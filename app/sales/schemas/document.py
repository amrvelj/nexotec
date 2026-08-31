"""SalesDocument schemas (WP-8 PR-7)."""

import datetime as dt
import uuid

from app.core.schemas import CamelModel
from app.sales.models.document import DocumentOwnerType


class DocumentRead(CamelModel):
    id: uuid.UUID
    owner_type: DocumentOwnerType
    owner_id: uuid.UUID
    version: int
    correspondence_language: str
    rendered_at: dt.datetime
    rendered_by: uuid.UUID | None


class DocumentPage(CamelModel):
    items: list[DocumentRead]
