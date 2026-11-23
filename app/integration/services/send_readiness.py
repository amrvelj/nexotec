"""What the send dialog reads from integration before a send (KAN-217 PR-6;
D-A-41, FR-A-51–54; ADR-206).

**`channel_readiness`** — per channel, whether this author can send on it in
this Betrieb right now: e-mail through the mailbox `resolve_sender` would
pick (the author's own, else the shared one; its refusal reason otherwise),
SMS and WhatsApp through the Betrieb's enabled provider connection
(`messaging_not_connected` otherwise). The dialog offers only the channels
that are ready (FR-A-52, «connected channels only») and names why the others
are not. A SELECT only; nothing is written, no provider is called.

**`latest_deliveries`** — the newest delivery row per document, as plain
values, for the delivery chip on the document (FR-A-51). One query for a
document list.
"""

import dataclasses
import datetime as dt
import uuid
from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.integration.adapters.messaging_base import MessagingChannel
from app.integration.models.mail import MessageDelivery
from app.integration.services import messaging
from app.integration.services.mail_sending import resolve_sender
from app.platform.public import MessageChannel

MESSAGING_NOT_CONNECTED = "messaging_not_connected"


@dataclasses.dataclass(frozen=True)
class ChannelReadiness:
    channel: MessageChannel
    ready: bool
    # The closed code of why it is not ready (`no_mailbox_connected`,
    # `own_mailbox_in_error`, `business_mailbox_in_error`,
    # `messaging_not_connected`); None when ready.
    reason: str | None
    # The address an e-mail would leave from; None on SMS and WhatsApp.
    sender_address: str | None = None


@dataclasses.dataclass(frozen=True)
class DeliverySummary:
    delivery_id: uuid.UUID
    document_id: uuid.UUID
    channel: str
    status: str
    sent_at: dt.datetime
    failure_code: str | None
    provider_status: str | None


def _email_readiness(db: Session, *, tenant_id: uuid.UUID, author_user_id: uuid.UUID) -> ChannelReadiness:
    try:
        sender = resolve_sender(db, dealership_id=tenant_id, author_user_id=author_user_id)
    except ConflictError as exc:
        reason = (exc.details or {}).get("reason")
        return ChannelReadiness(channel=MessageChannel.EMAIL, ready=False, reason=str(reason) if reason else None)
    return ChannelReadiness(channel=MessageChannel.EMAIL, ready=True, reason=None, sender_address=sender.address)


def channel_readiness(
    db: Session, *, tenant_id: uuid.UUID, author_user_id: uuid.UUID
) -> Mapping[MessageChannel, ChannelReadiness]:
    """Every channel, ready or with its reason (the module docstring)."""

    readiness: dict[MessageChannel, ChannelReadiness] = {
        MessageChannel.EMAIL: _email_readiness(db, tenant_id=tenant_id, author_user_id=author_user_id)
    }
    for messaging_channel in MessagingChannel:
        channel = MessageChannel(messaging_channel.value)
        connected = messaging.connection_for(db, tenant_id, messaging_channel) is not None
        readiness[channel] = ChannelReadiness(
            channel=channel, ready=connected, reason=None if connected else MESSAGING_NOT_CONNECTED
        )
    return readiness


def latest_deliveries(
    db: Session, *, tenant_id: uuid.UUID, document_ids: Iterable[uuid.UUID]
) -> Mapping[uuid.UUID, DeliverySummary]:
    """The newest delivery of each document sent in this Betrieb; a document
    never sent has no entry. Another Betrieb's rows are never read."""

    ids = list(dict.fromkeys(document_ids))
    if not ids:
        return {}
    rows = db.scalars(
        select(MessageDelivery)
        .where(MessageDelivery.tenant_id == tenant_id, MessageDelivery.document_id.in_(ids))
        .order_by(MessageDelivery.sent_at.desc(), MessageDelivery.id.desc())
    ).all()
    latest: dict[uuid.UUID, DeliverySummary] = {}
    for row in rows:
        assert row.document_id is not None  # filtered by document_id above
        if row.document_id in latest:
            continue
        latest[row.document_id] = DeliverySummary(
            delivery_id=row.id,
            document_id=row.document_id,
            channel=row.channel.value,
            status=row.status.value,
            sent_at=row.sent_at,
            failure_code=row.failure_code,
            provider_status=row.provider_status,
        )
    return latest
