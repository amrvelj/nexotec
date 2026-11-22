"""SalesDocument schemas (WP-8 PR-7)."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.platform.public import MessageChannel
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


class DocumentSendRequest(CamelModel):
    """KAN-217 PR-5: send one document to its customer. `text` replaces the
    standard text on e-mail and SMS; WhatsApp sends its approved template."""

    channel: MessageChannel
    text: str | None = Field(default=None, min_length=1, max_length=20000)


class MessageDeliveryRead(CamelModel):
    """The delivery record the send wrote (integration's row, as values)."""

    delivery_id: uuid.UUID
    channel: str
    status: str
    sender_address: str | None
    failure_code: str | None
    provider_status: str | None
