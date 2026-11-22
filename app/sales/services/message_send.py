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

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage, format_date_ch
from app.customer.public import message_recipient
from app.integration.public import (
    DeliveryRecord,
    MailAttachment,
    WhatsAppTemplate,
    check_deliverable,
    deliver_message,
    refuse_unless_allowed,
)
from app.platform.public import (
    MessageChannel,
    MessageDelivery,
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
    document = get_document_or_404(db, tenant_id=tenant_id, document_id=document_id)
    owner = _owner(db, document)
    message_type = get_message_type(_MESSAGE_TYPE[document.owner_type])
    assert message_type is not None  # registered at start-up by app/message_type_registry.py
    if owner.customer_id is None:
        raise ConflictError("This document names no customer to send it to.", details={"reason": NO_CUSTOMER})

    recipient = message_recipient(
        db, group_id=group_id, customer_id=owner.customer_id, channel=channel, purpose=message_type.purpose
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
    values: dict[str, str | None] = {
        Placeholder.DOCUMENT_NUMBER.value: number,
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
    if rendered is not None and rendered.language != language:
        # The library fell back to another language (the Betrieb has no text
        # in the customer's): the whole message follows the text — opening,
        # subject, the link's page and the WhatsApp template's language — so
        # it never mixes two languages.
        language = rendered.language
        rendered = render(language)
    text = text_override if text_override is not None else (rendered.text if rendered is not None else None)
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
