"""The customer message types Sales registers with the platform's send dialog
(KAN-217 PR-1; Dealer Administration FR-A-52, FR-A-53).

Three document types, each transactional — sending an offer, an order
confirmation or a contract the customer asked for is contract preparation,
not marketing (Anto, 2026-11-14, Q1 A) — each sent on e-mail (the PDF
attached), SMS and WhatsApp (a secure link). The texts are platform seeds in
four languages (`app/platform/documents/services/message_text_seed.py`; the
offer's e-mail body is the KAN-208 seed `email.offer_sent`).

Registered by the composition root `app/message_type_registry.py`, never at
import time. Sales has no order-confirmation document yet: the type is
registered so its texts and the dialog's contract exist when it is built.
"""

from app.platform.public import (
    MessageChannel,
    MessagePurpose,
    MessageType,
    MessageTypeKind,
    StandardTextAppliesTo,
    register_document_source,
    register_message_type,
)
from app.sales.services.document import linked_contract_document, linked_offer_document

OFFER = "offer"
ORDER_CONFIRMATION = "order_confirmation"
SALES_CONTRACT = "sales_contract"

_EMAIL, _SMS, _WHATSAPP = MessageChannel.EMAIL, MessageChannel.SMS, MessageChannel.WHATSAPP

SALES_MESSAGE_TYPES: tuple[MessageType, ...] = (
    MessageType(
        code=OFFER,
        module="sales",
        kind=MessageTypeKind.DOCUMENT,
        purpose=MessagePurpose.TRANSACTIONAL,
        applies_to=StandardTextAppliesTo.OFFER,
        text_codes={_EMAIL: "email.offer_sent", _SMS: "sms.offer_sent", _WHATSAPP: "whatsapp.offer_sent"},
    ),
    MessageType(
        code=ORDER_CONFIRMATION,
        module="sales",
        kind=MessageTypeKind.DOCUMENT,
        purpose=MessagePurpose.TRANSACTIONAL,
        applies_to=StandardTextAppliesTo.ORDER_CONFIRMATION,
        text_codes={
            _EMAIL: "email.order_confirmation_sent",
            _SMS: "sms.order_confirmation_sent",
            _WHATSAPP: "whatsapp.order_confirmation_sent",
        },
    ),
    MessageType(
        code=SALES_CONTRACT,
        module="sales",
        kind=MessageTypeKind.DOCUMENT,
        purpose=MessagePurpose.TRANSACTIONAL,
        applies_to=StandardTextAppliesTo.CONTRACT,
        text_codes={_EMAIL: "email.contract_sent", _SMS: "sms.contract_sent", _WHATSAPP: "whatsapp.contract_sent"},
    ),
)


def register_message_types() -> None:
    for message_type in SALES_MESSAGE_TYPES:
        register_message_type(message_type)
    # KAN-217 PR-4: the documents a secure link (SMS, WhatsApp) may serve. The
    # order confirmation has no document yet, so no link can name one.
    register_document_source(OFFER, linked_offer_document)
    register_document_source(SALES_CONTRACT, linked_contract_document)
