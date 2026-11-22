"""`deliver_message` (KAN-217 PR-5; D-A-41, FR-A-51–54, A-RULE-10; ADR-204) —
the one door a customer message leaves Nexotec through, on every channel.

The module that owns the document (Sales today) reads the recipient and the
consent verdict from `customer.public.message_recipient`, renders the text
through `platform.public.render_standard_text`, and hands both here. This
function:

1. **refuses without a verdict** — the verdict's channel and purpose must be
   the message's, and a refused verdict is 409 `recipient_refused` with the
   verdict's own reason. Consent is decided in Customers and never stubbed:
   an architecture test holds every call site in `app/` to pass a verdict it
   took from `message_recipient` (`test_kan217_delivery_takes_customers_verdict.py`);
2. **refuses a channel the type is not sent on** (422 `channel_not_offered`);
3. **e-mail** — sends from the resolved mailbox (`mail_sending`, KAN-215: the
   author's own, else the Betrieb's shared one, else a 409 naming both
   screens), the PDF attached;
4. **SMS** — the Betrieb's Twilio connection (none → 409
   `messaging_not_connected`, before anything is written), the text with the
   secure link on its own line (the caller appends it);
5. **WhatsApp** — an **approved template only** (FR-A-54). The 24-hour
   customer-service window opens only when the customer writes in, and
   Nexotec receives no inbound message yet, so every send is outside it: no
   approved template → 409 `whatsapp_template_not_approved`, free text is
   never sent;
6. writes **exactly one `integration_message_delivery` row** per send on a
   session of its own (ADR-047) — success or failure; a provider failure is a
   `failed` row with a closed code, never the caller's 500. The caller's
   session is neither written nor committed.

Delivery reports (the providers' status callbacks) update the row in a later
PR (KAN-217 PR-7, ADR-204).
"""

import dataclasses
import logging
import uuid
from collections.abc import Sequence
from typing import Any, Protocol, cast

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError, UnprocessableEntityError
from app.integration.adapters.mail_base import MailAttachment
from app.integration.adapters.messaging_base import (
    MessagingChannel,
    OutboundMessage,
    SmsMessage,
    WhatsAppTemplateMessage,
    check_message,
)
from app.integration.models.mail import DeliveryChannel, DeliveryStatus, MessageDelivery
from app.integration.services import messaging
from app.integration.services.mail_sending import DeliveryRecord, failure_code_of, send_customer_mail
from app.platform.public import MessageChannel, get_message_type

logger = logging.getLogger(__name__)

RECIPIENT_REFUSED = "recipient_refused"
CHANNEL_NOT_OFFERED = "channel_not_offered"
WHATSAPP_TEMPLATE_NOT_APPROVED = "whatsapp_template_not_approved"
MESSAGE_INVALID = "message_invalid"
# `integration_message_delivery.customer_label` (a first and a last name may be 201 characters).
LABEL_MAX = 200


class ConsentVerdict(Protocol):
    """The shape of `customer.public.MessageRecipient`: integration sits below
    Customers (ADR-131), so it names the fields it reads, never the class."""

    @property
    def customer_id(self) -> uuid.UUID: ...
    @property
    def channel(self) -> Any: ...
    @property
    def purpose(self) -> Any: ...
    @property
    def address(self) -> str | None: ...
    @property
    def display_name(self) -> str: ...
    @property
    def allowed(self) -> bool: ...
    @property
    def refusal(self) -> str | None: ...


@dataclasses.dataclass(frozen=True)
class WhatsAppTemplate:
    """An approved template as the Betrieb's provider holds it: its name, the
    language it was approved in, and the body's `{{1}}`, `{{2}}` … values."""

    name: str
    language: str
    parameters: tuple[str, ...] = ()


def refuse_unless_allowed(*, recipient: ConsentVerdict) -> None:
    """Customers' verdict, enforced: 409 `recipient_refused` with its own
    reason. The caller asks this first, straight after reading the verdict, so
    a refused customer is told so before any other answer."""

    if not recipient.allowed or not recipient.address:
        raise ConflictError(
            "This customer cannot be sent this message on this channel.",
            details={
                "reason": RECIPIENT_REFUSED,
                "refusal": recipient.refusal,
                "channel": MessageChannel(recipient.channel).value,
            },
        )


def _outbound(
    channel: MessagingChannel,
    *,
    address: str,
    text: str,
    whatsapp_template: WhatsAppTemplate | None,
    link_url: str | None,
) -> OutboundMessage:
    """The message the provider receives. The secure link goes on SMS on a
    line of its own after the text, on WhatsApp as the template's last
    parameter (ADR-204) — and never into the delivery record."""

    if channel == MessagingChannel.WHATSAPP:
        assert whatsapp_template is not None  # check_deliverable refused its absence
        parameters = whatsapp_template.parameters + ((link_url,) if link_url else ())
        return WhatsAppTemplateMessage(
            to=address,
            template_name=whatsapp_template.name,
            language=whatsapp_template.language,
            body_parameters=parameters,
        )
    return SmsMessage(to=address, body=f"{text.rstrip()}\n{link_url}" if link_url else text)


def check_deliverable(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    message_type: str,
    recipient: ConsentVerdict,
    text: str,
    whatsapp_template: WhatsAppTemplate | None,
    link_url: str | None,
) -> MessageChannel:
    """Every refusal `deliver_message` raises before it sends, in its order,
    and the channel when none applies. A caller asks first when it must write
    something for the send (the secure link) that a refused send should not
    leave behind — then `link_url` is a sample of the same length
    (`platform.public.sample_document_link_url`); `deliver_message` asks again
    with the real one."""

    registered = get_message_type(message_type)
    if registered is None:
        raise ValueError(f"Message type {message_type!r} is not registered.")
    channel = MessageChannel(recipient.channel)
    if recipient.purpose != registered.purpose:
        # A verdict read for another purpose is the caller's bug: a marketing
        # type must never travel on a transactional verdict.
        raise ValueError(f"The verdict was read for {recipient.purpose!r}, the type is {registered.purpose.value!r}.")
    if channel not in registered.channels:
        raise UnprocessableEntityError(
            f"A {message_type} is not sent on {channel.value}.",
            details={"reason": CHANNEL_NOT_OFFERED, "channel": channel.value},
        )
    refuse_unless_allowed(recipient=recipient)
    if channel == MessageChannel.EMAIL:
        return channel
    messaging_channel = MessagingChannel(channel.value)
    if messaging.connection_for(db, tenant_id, messaging_channel) is None:
        raise messaging.not_connected(messaging_channel)
    if messaging_channel == MessagingChannel.WHATSAPP and whatsapp_template is None:
        raise ConflictError(
            "WhatsApp sends only a text the provider approved as a template; this one is not approved.",
            details={"reason": WHATSAPP_TEMPLATE_NOT_APPROVED, "link": "/administration/texts"},
        )
    # The message's shape (a text too long with its link, say) is refused
    # before any link, provider call or delivery row.
    message = _outbound(
        messaging_channel,
        address=cast(str, recipient.address),
        text=text,
        whatsapp_template=whatsapp_template,
        link_url=link_url,
    )
    try:
        check_message(message)
    except ValueError as exc:
        raise UnprocessableEntityError(
            str(exc), details={"reason": MESSAGE_INVALID, "channel": messaging_channel.value}
        ) from exc
    return channel


def deliver_message(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    author_user_id: uuid.UUID,
    message_type: str,
    document_id: uuid.UUID | None,
    recipient: ConsentVerdict,
    text: str,
    subject: str | None = None,
    attachments: Sequence[MailAttachment] = (),
    whatsapp_template: WhatsAppTemplate | None = None,
    document_link_id: uuid.UUID | None = None,
    link_url: str | None = None,
) -> DeliveryRecord:
    """One message to one customer (the module docstring). `text` is what the
    delivery record keeps; `link_url` (SMS, WhatsApp) is added to the message
    only — the raw token is never stored (ADR-193). Raises its refusals
    before anything is written; afterwards it answers, never raises: the
    delivery row says what happened."""

    channel = check_deliverable(
        db,
        tenant_id=tenant_id,
        message_type=message_type,
        recipient=recipient,
        text=text,
        whatsapp_template=whatsapp_template,
        link_url=link_url,
    )
    if channel == MessageChannel.EMAIL:
        if not subject:
            raise ValueError("An e-mail needs a subject.")
        return send_customer_mail(
            db,
            dealership_id=tenant_id,
            author_user_id=author_user_id,
            document_type=message_type,
            document_id=document_id,
            to=[cast(str, recipient.address)],
            subject=subject,
            body_text=text,
            attachments=attachments,
            customer_id=recipient.customer_id,
            customer_label=recipient.display_name[:LABEL_MAX],
        )

    messaging_channel = MessagingChannel(channel.value)
    message = _outbound(
        messaging_channel,
        address=cast(str, recipient.address),
        text=text,
        whatsapp_template=whatsapp_template,
        link_url=link_url,
    )
    return _send_on_own_session(
        db,
        tenant_id=tenant_id,
        author_user_id=author_user_id,
        message_type=message_type,
        document_id=document_id,
        document_link_id=document_link_id,
        recipient=recipient,
        channel=messaging_channel,
        message=message,
        text=text,
    )


def _send_on_own_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    author_user_id: uuid.UUID,
    message_type: str,
    document_id: uuid.UUID | None,
    document_link_id: uuid.UUID | None,
    recipient: ConsentVerdict,
    channel: MessagingChannel,
    message: OutboundMessage,
    text: str,
) -> DeliveryRecord:
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        connection = messaging.connection_for(own, tenant_id, channel)
        if connection is None:  # switched off between the check and now
            raise messaging.not_connected(channel)
        connection_id = connection.id
        provider_message_id: str | None = None
        provider_status: str | None = None
        failure: BaseException | None = None
        try:
            sent = messaging.send(
                own, tenant_id=tenant_id, message=message, actor_id=author_user_id, purpose=message_type
            )
            provider_message_id, provider_status = sent.provider_message_id, sent.provider_status
        except Exception as exc:  # noqa: BLE001 — every failure is a failed row, never the caller's 500
            failure = exc
            logger.warning(
                "integration.messaging: %s send on connection %s failed (%s)",
                channel.value,
                connection_id,
                type(exc).__name__,
                extra={"connectionId": str(connection_id)},
            )
        own.rollback()  # whatever the gateway left open
        now = utcnow()
        delivery = MessageDelivery(
            tenant_id=tenant_id,
            channel=DeliveryChannel(channel.value),
            connection_id=connection_id,
            recipients=[cast(str, recipient.address)],
            body_text=text,
            document_type=message_type,
            document_id=document_id,
            document_link_id=document_link_id,
            customer_id=recipient.customer_id,
            customer_label=recipient.display_name[:LABEL_MAX],
            customer_label_refreshed_at=now,
            author_user_id=author_user_id,
            status=DeliveryStatus.FAILED if failure is not None else DeliveryStatus.SENT,
            failure_code=failure_code_of(failure) if failure is not None else None,
            provider_message_id=provider_message_id,
            provider_status=(provider_status or None) and provider_status[:32],
            sent_at=now,
            created_by=author_user_id,
            updated_by=author_user_id,
        )
        own.add(delivery)
        own.commit()
        return DeliveryRecord(
            delivery_id=delivery.id,
            status=delivery.status,
            sender_address=None,
            failure_code=delivery.failure_code,
            channel=delivery.channel,
            provider_status=delivery.provider_status,
        )
