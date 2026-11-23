"""SalesDocument schemas (WP-8 PR-7)."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.platform.public import MessageChannel
from app.sales.models.document import DocumentOwnerType


class DocumentDeliveryRead(CamelModel):
    """KAN-217 PR-6: the newest send of a document (integration's delivery
    row, as values) — the delivery chip on the document (FR-A-51)."""

    delivery_id: uuid.UUID
    channel: str
    status: str
    sent_at: dt.datetime
    failure_code: str | None
    provider_status: str | None


class DocumentRead(CamelModel):
    id: uuid.UUID
    owner_type: DocumentOwnerType
    owner_id: uuid.UUID
    version: int
    correspondence_language: str
    rendered_at: dt.datetime
    rendered_by: uuid.UUID | None
    # The newest send of this version; None while it was never sent.
    last_delivery: DocumentDeliveryRead | None = None


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


class SendChannelOptionRead(CamelModel):
    """One channel of the send dialog (KAN-217 PR-6). `blocked` is the first
    refusal the send would give now (`recipient_refused` with `refusal`, a
    channel not ready, `whatsapp_template_not_approved`,
    `message_text_missing`); None when the send may go."""

    channel: MessageChannel
    ready: bool
    not_ready_reason: str | None
    sender_address: str | None
    address: str | None
    allowed: bool
    refusal: str | None
    language: str
    text: str | None
    text_editable: bool
    delivery: str
    blocked: str | None


class SendOptionsRead(CamelModel):
    document_id: uuid.UUID
    customer_label: str
    preselected_channel: MessageChannel | None
    channels: list[SendChannelOptionRead]
