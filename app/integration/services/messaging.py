"""`send(tenant, message)` (KAN-217 PR-3, D-A-41, A-RULE-10) — the one place
an SMS or a WhatsApp message meets the Betrieb's own provider account.

It resolves the Betrieb's connection for the message's channel (SMS →
Twilio, WhatsApp → Meta; `messaging_base.CHANNEL_PROVIDER`), and sends the
message through `call_capability` under `messaging_send`: the request is
logged (operation, HTTP status, the provider's error code — never the number
or the text), a failing provider charges the breaker of that connection, and
the usage view counts the request under its operation name.

**What it does not do** — `deliver_message` (PR-5) does, before it calls this:
read the recipient and the consent verdict from `contact.public`, apply the
WhatsApp template rule, and write `message_delivery`. This function is not in
`integration.public`: no module above may send without that verdict.

**Which connection.** One per provider per Betrieb (the registry's unique
index; neither provider has a sandbox), and it must be enabled. None → 409
`messaging_not_connected`. Every provider failure passes through in the
`app.integration.errors` vocabulary, so the caller can record it.
"""

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.integration.adapters.messaging_base import (
    CHANNEL_PROVIDER,
    MESSAGING_SEND_CAPABILITY,
    MessagingChannel,
    MessagingGateway,
    OutboundMessage,
    SentMessage,
    check_message,
)
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.services.gateway import call_capability


def connection_for(db: Session, tenant_id: uuid.UUID, channel: MessagingChannel) -> IntegrationConnection | None:
    return db.scalar(
        select(IntegrationConnection)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.scope == ConnectionScope.TENANT,
            IntegrationConnection.environment == ConnectionEnvironment.PRODUCTION,
            IntegrationConnection.enabled.is_(True),
            IntegrationProvider.provider_code == CHANNEL_PROVIDER[channel],
        )
    )


def not_connected(channel: MessagingChannel) -> ConflictError:
    return ConflictError(
        f"No {channel.value} provider is connected for this business.",
        details={
            "reason": "messaging_not_connected",
            "channel": channel.value,
            "link": "/administration/integrations",
        },
    )


def connected_channels(db: Session, *, tenant_id: uuid.UUID) -> frozenset[MessagingChannel]:
    """The channels the Betrieb has an enabled connection for — the send
    dialog offers only these (PR-6)."""

    return frozenset(channel for channel in MessagingChannel if connection_for(db, tenant_id, channel) is not None)


def send(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    message: OutboundMessage,
    actor_id: uuid.UUID | None,
    purpose: str,
    correlation_id: uuid.UUID | None = None,
) -> SentMessage:
    """One message through the Betrieb's own account. The caller's session is
    the caller's: nothing here commits it (ADR-047); the call log and the
    secret's audit row are written on the gateway's own session."""

    # A malformed message is the caller's bug: refused before the gateway, so
    # it is never logged as a provider call nor charged to the breaker.
    check_message(message)
    connection = connection_for(db, tenant_id, message.channel)
    if connection is None:
        raise not_connected(message.channel)
    with call_capability(
        db,
        connection=connection,
        capability=MESSAGING_SEND_CAPABILITY,
        correlation_id=correlation_id,
        actor_id=actor_id,
        purpose=purpose,
    ) as adapter:
        return cast(MessagingGateway, adapter).send(message)
