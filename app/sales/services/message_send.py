"""Sending an offer or contract document to the customer (KAN-217 PR-5;
Dealer Administration D-A-41, FR-A-51–54, A-RULE-10; ADR-204).

`POST /v1/sales/documents/{id}/send` (`channel`, optional `text`). Sales owns
the document, so Sales runs the send — every call goes down the layers:

1. the document, in the acting Betrieb (another Betrieb's is 404), and its
   offer or contract: the customer, the number, the vehicle;
2. **the recipient and the consent verdict** from Customers
   (`customer.public.message_recipient`): the address on the channel, the
   correspondence language, the letter opening and whether this send may go;
   a refusal is 409 `recipient_refused` with Customers' reason, nothing sent;
3. **the text** from platform's library (`render_standard_text`, the type's
   code per channel) in the customer's language — or the language the
   library fell back to, which the whole message then follows. The author's own wording
   (`text`) replaces it on e-mail and SMS; WhatsApp sends the approved
   template exactly (422 `whatsapp_text_fixed`);
4. **the document** — e-mail attaches the PDF; SMS and WhatsApp carry a
   secure link (`issue_document_link`), which integration adds to the
   message (SMS: its own line; WhatsApp: the template's last parameter) and
   never to the delivery record;
5. `integration.public.deliver_message` — sender, provider, template rule
   and the delivery record.

**ADR-047.** Each side commits its own work: platform the secure link (on its
own session, before the provider is called, so the customer can open it as
soon as it arrives), integration the delivery row. Sales writes nothing. A send that fails after the link was issued leaves an
unused link that expires in 14 days — the compensation is its expiry, and
it serves only the document the customer was being sent.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage, format_date_ch
from app.customer.public import MessageRecipient, message_recipient, preselected_message_channel
from app.integration.public import (
    DeliveryRecord,
    MailAttachment,
    WhatsAppTemplate,
    channel_readiness,
    check_deliverable,
    deliver_message,
    refuse_unless_allowed,
)
from app.platform.public import (
    MessageChannel,
    MessageDelivery,
    MessageType,
    Placeholder,
    RenderedStandardText,
    StandardTextSlot,
    delivery_of,
    get_active_user_acting_in,
    get_message_type,
    issue_document_link,
    render_standard_text,
    sample_document_link_url,
    text_category_of,
)
from app.sales.i18n import t
from app.sales.message_types import OFFER, SALES_CONTRACT
from app.sales.models.contract import SalesContract
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.models.offer import SalesOffer
from app.sales.services.document import get_document_or_404, render_document_pdf

WHATSAPP_TEXT_FIXED = "whatsapp_text_fixed"
NO_CUSTOMER = "document_has_no_customer"
TEXT_MISSING = "message_text_missing"

_MESSAGE_TYPE = {DocumentOwnerType.OFFER: OFFER, DocumentOwnerType.CONTRACT: SALES_CONTRACT}
_TITLE_KEY = {DocumentOwnerType.OFFER: "offer.title", DocumentOwnerType.CONTRACT: "contract.title"}


def _owner(db: Session, document: SalesDocument) -> SalesOffer | SalesContract:
    owner: SalesOffer | SalesContract | None
    if document.owner_type == DocumentOwnerType.OFFER:
        owner = db.scalar(
            select(SalesOffer).where(SalesOffer.id == document.owner_id, SalesOffer.tenant_id == document.tenant_id)
        )
    else:
        owner = db.scalar(
            select(SalesContract).where(
                SalesContract.id == document.owner_id, SalesContract.tenant_id == document.tenant_id
            )
        )
    if owner is None:  # a document always has its owner; a missing one is treated as not found
        raise NotFoundError(f"Document {document.id} was not found.")
    return owner


def _number(owner: SalesOffer | SalesContract) -> str:
    return owner.offer_number if isinstance(owner, SalesOffer) else owner.contract_number


def _author_name(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    """The sender signs the message (`{{user.full_name}}`): the signed-in
    author, who acts in this Betrieb."""

    user = get_active_user_acting_in(db, dealership_id=tenant_id, user_id=user_id)
    return user.label if user is not None else None


def _document_and_type(
    db: Session, *, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[SalesDocument, SalesOffer | SalesContract, MessageType, uuid.UUID]:
    """The document in the acting Betrieb (404 otherwise), its owner, its
    registered message type and its customer (409 without one)."""

    document = get_document_or_404(db, tenant_id=tenant_id, document_id=document_id)
    owner = _owner(db, document)
    message_type = get_message_type(_MESSAGE_TYPE[document.owner_type])
    assert message_type is not None  # registered at start-up by app/message_type_registry.py
    if owner.customer_id is None:
        raise ConflictError("This document names no customer to send it to.", details={"reason": NO_CUSTOMER})
    return document, owner, message_type, owner.customer_id


def _text(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    author_user_id: uuid.UUID,
    document: SalesDocument,
    owner: SalesOffer | SalesContract,
    message_type: MessageType,
    recipient: MessageRecipient,
    channel: MessageChannel,
    text_override: str | None,
) -> tuple[SwissLanguage, RenderedStandardText | None, str | None]:
    """The language the message is in, the library's rendered text and the
    text that is sent — the send and the dialog's preview read the same."""

    values: dict[str, str | None] = {
        Placeholder.DOCUMENT_NUMBER.value: _number(owner),
        Placeholder.DOCUMENT_DATE.value: format_date_ch(document.rendered_at.date()),
        Placeholder.VEHICLE_MAKE_MODEL.value: owner.vehicle_label,
        Placeholder.USER_FULL_NAME.value: _author_name(db, tenant_id=tenant_id, user_id=author_user_id),
    }

    def render(language: SwissLanguage) -> RenderedStandardText | None:
        return render_standard_text(
            db,
            dealership_id=tenant_id,
            category=text_category_of(channel),
            applies_to=message_type.applies_to,
            slot=StandardTextSlot.BODY,
            language=language,
            values={**values, Placeholder.CUSTOMER_SALUTATION.value: recipient.salutations[language.value]},
            location_id=document.location_id,
            code=message_type.text_codes[channel],
        )

    language = SwissLanguage(recipient.language)
    rendered = render(language)
    if text_override is None and rendered is not None and rendered.language != language:
        # The library fell back to another language (the Betrieb has no text
        # in the customer's) and the author wrote nothing of their own: the
        # whole message follows the text — opening,
        # subject, the link's page and the WhatsApp template's language — so
        # it never mixes two languages.
        language = rendered.language
        rendered = render(language)
    text = text_override if text_override is not None else (rendered.text if rendered is not None else None)
    return language, rendered, text


def send_document(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    author_user_id: uuid.UUID,
    document_id: uuid.UUID,
    channel: MessageChannel,
    text_override: str | None,
) -> DeliveryRecord:
    document, owner, message_type, customer_id = _document_and_type(db, tenant_id=tenant_id, document_id=document_id)

    recipient = message_recipient(
        db, group_id=group_id, customer_id=customer_id, channel=channel, purpose=message_type.purpose
    )
    if recipient is None:  # the customer left the group (a merge is followed, never this)
        raise ConflictError("This document names no customer to send it to.", details={"reason": NO_CUSTOMER})
    # Customers' refusal is the first answer a refused customer gets.
    refuse_unless_allowed(recipient=recipient)
    if channel == MessageChannel.WHATSAPP and text_override is not None:
        raise UnprocessableEntityError(
            "WhatsApp sends the approved template as it is; its text cannot be changed here.",
            details={"reason": WHATSAPP_TEXT_FIXED},
        )
    number = _number(owner)
    language, rendered, text = _text(
        db,
        tenant_id=tenant_id,
        author_user_id=author_user_id,
        document=document,
        owner=owner,
        message_type=message_type,
        recipient=recipient,
        channel=channel,
        text_override=text_override,
    )
    if not text or not text.strip():
        raise ConflictError("There is no text to send on this channel.", details={"reason": TEXT_MISSING})

    subject: str | None = None
    attachments: tuple[MailAttachment, ...] = ()
    template: WhatsAppTemplate | None = None
    link_id: uuid.UUID | None = None
    link_url: str | None = None
    if delivery_of(message_type, channel) == MessageDelivery.ATTACHMENT:
        subject = t(language, _TITLE_KEY[document.owner_type], number=number)
        attachments = (
            MailAttachment(
                filename=f"{number}.pdf".replace("/", "-"),
                content_type="application/pdf",
                content=render_document_pdf(db, document=document, dealership_id=tenant_id),
            ),
        )
    else:
        if (
            channel == MessageChannel.WHATSAPP
            and rendered is not None
            and rendered.template_approved
            and rendered.provider_template_id
        ):
            template = WhatsAppTemplate(
                name=rendered.provider_template_id, language=rendered.language.value, parameters=rendered.parameters
            )
        # A refused send leaves no link behind: every refusal of integration's,
        # the message's shape included (checked with a link of the real
        # length), comes before the link is issued.
        check_deliverable(
            db,
            tenant_id=tenant_id,
            message_type=message_type.code,
            recipient=recipient,
            text=text,
            whatsapp_template=template,
            link_url=sample_document_link_url(language),
        )
        link = issue_document_link(
            db,
            tenant_id=tenant_id,
            message_type=message_type.code,
            document_id=document.id,
            actor_id=author_user_id,
            language=language,
        )
        link_id, link_url = link.link_id, link.url

    return deliver_message(
        db,
        tenant_id=tenant_id,
        author_user_id=author_user_id,
        message_type=message_type.code,
        document_id=document.id,
        recipient=recipient,
        text=text,
        subject=subject,
        attachments=attachments,
        whatsapp_template=template,
        document_link_id=link_id,
        link_url=link_url,
    )


# -- the dialog's read (KAN-217 PR-6) ------------------------------------------------

RECIPIENT_REFUSED = "recipient_refused"
WHATSAPP_TEMPLATE_NOT_APPROVED = "whatsapp_template_not_approved"


@dataclasses.dataclass(frozen=True)
class ChannelOption:
    """One channel of the send dialog, as the send would answer it now.

    `blocked` is the first refusal the send without a text of the author's
    own would give, in the send's own order: Customers' verdict
    (`recipient_refused`, with `refusal`), then no text
    (`message_text_missing` — the author's own wording lifts it on e-mail and
    SMS), then the channel not ready (`no_mailbox_connected`,
    `own_mailbox_in_error`, `messaging_not_connected` …), then a WhatsApp text
    without an approved template. Not predicted: a message the provider's
    shape refuses (`message_invalid`, an SMS too long once the link is
    added) — the send says it."""

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
    delivery: MessageDelivery
    blocked: str | None


@dataclasses.dataclass(frozen=True)
class SendOptions:
    document_id: uuid.UUID
    customer_label: str
    preselected_channel: MessageChannel | None
    channels: tuple[ChannelOption, ...]


def send_options(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    author_user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> SendOptions:
    """`GET /v1/sales/documents/{id}/send-options` — every channel the
    document's type is sent on, with the recipient, Customers' verdict, the
    channel's readiness and the text the send would carry (FR-A-52/53). A
    read: no link is issued, nothing is written, no provider is called.

    The pre-selected channel is Customers' (`preselected_message_channel`)
    when it is offered and ready; otherwise none — the dialog never opens on
    a channel it cannot send on (FR-A-52)."""

    document, owner, message_type, customer_id = _document_and_type(db, tenant_id=tenant_id, document_id=document_id)
    readiness = channel_readiness(db, tenant_id=tenant_id, author_user_id=author_user_id)
    options: list[ChannelOption] = []
    customer_label: str | None = None
    for channel in message_type.channels:
        recipient = message_recipient(
            db, group_id=group_id, customer_id=customer_id, channel=channel, purpose=message_type.purpose
        )
        if recipient is None:  # the customer left the group, as the send answers
            raise ConflictError("This document names no customer to send it to.", details={"reason": NO_CUSTOMER})
        customer_label = recipient.display_name
        language, rendered, text = _text(
            db,
            tenant_id=tenant_id,
            author_user_id=author_user_id,
            document=document,
            owner=owner,
            message_type=message_type,
            recipient=recipient,
            channel=channel,
            text_override=None,
        )
        ready = readiness[channel]
        blocked: str | None = None
        if not recipient.allowed or not recipient.address:
            blocked = RECIPIENT_REFUSED
        elif not text or not text.strip():
            # Refused only without the author's own wording: the dialog lets
            # the author write one (e-mail and SMS) and then sends it.
            blocked = TEXT_MISSING
        elif not ready.ready:
            blocked = ready.reason
        elif channel == MessageChannel.WHATSAPP and not (
            rendered is not None and rendered.template_approved and rendered.provider_template_id
        ):
            blocked = WHATSAPP_TEMPLATE_NOT_APPROVED
        options.append(
            ChannelOption(
                channel=channel,
                ready=ready.ready,
                not_ready_reason=ready.reason,
                sender_address=ready.sender_address,
                address=recipient.address,
                allowed=recipient.allowed,
                refusal=recipient.refusal,
                language=language.value,
                text=text,
                text_editable=channel != MessageChannel.WHATSAPP,
                delivery=delivery_of(message_type, channel),
                blocked=blocked,
            )
        )
    _found, preferred = preselected_message_channel(db, group_id=group_id, customer_id=customer_id)
    ready_channels = {option.channel for option in options if option.ready}
    return SendOptions(
        document_id=document.id,
        customer_label=customer_label or owner.customer_label or "",
        preselected_channel=preferred if preferred in ready_channels else None,
        channels=tuple(options),
    )
