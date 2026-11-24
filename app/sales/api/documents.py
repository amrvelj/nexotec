"""Document generation/download endpoints (WP-8 PR-7). One shared router
for both owner types — offers and contracts render through the exact same
shared block vocabulary and the exact same render_document call.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_read, require_write
from app.db import get_db
from app.integration.public import latest_deliveries
from app.sales.models.document import DocumentOwnerType, SalesDocument
from app.sales.schemas.document import (
    DocumentDeliveryRead,
    DocumentPage,
    DocumentRead,
    DocumentSendRequest,
    MessageDeliveryRead,
    SendChannelOptionRead,
    SendOptionsRead,
)
from app.sales.services import contract as contract_service
from app.sales.services import document as document_service
from app.sales.services import message_send
from app.sales.services import offer as offer_service

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(
    tags=["sales"], route_class=IdempotentRoute, dependencies=[Depends(require_entitlement(ModuleCode.SALES))]
)


def _page(db: Session, *, tenant_id: uuid.UUID, documents: list[SalesDocument]) -> DocumentPage:
    """The version list, each with its newest send (KAN-217 PR-6, the
    delivery chip): one read of integration's delivery rows for the page."""

    sent = latest_deliveries(db, tenant_id=tenant_id, document_ids=[d.id for d in documents])
    items = []
    for document in documents:
        item = DocumentRead.model_validate(document, from_attributes=True)
        delivery = sent.get(document.id)
        if delivery is not None:
            item.last_delivery = DocumentDeliveryRead(
                delivery_id=delivery.delivery_id,
                channel=delivery.channel,
                status=delivery.status,
                sent_at=delivery.sent_at,
                failure_code=delivery.failure_code,
                provider_status=delivery.provider_status,
            )
        items.append(item)
    return DocumentPage(items=items)


@router.post("/sales/offers/{offer_id}/documents", response_model=DocumentRead, status_code=201)
def generate_offer_document(
    offer_id: uuid.UUID,
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    # Locked like every route that writes the deal row (KAN-147 order offer ->
    # contract -> deal): generating re-projects the row (KAN-408), which a
    # stale read would write back over a concurrent save.
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id, for_update=True)
    document = document_service.generate_offer_document(db, offer=offer, actor_id=principal.user_id)
    return DocumentRead.model_validate(document, from_attributes=True)


@router.get("/sales/offers/{offer_id}/documents", response_model=DocumentPage)
def list_offer_documents(
    offer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    documents = document_service.list_documents(
        db, tenant_id=principal.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer_id
    )
    return _page(db, tenant_id=principal.tenant_id, documents=list(documents))


@router.post("/sales/contracts/{contract_id}/documents", response_model=DocumentRead, status_code=201)
def generate_contract_document(
    contract_id: uuid.UUID,
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id, for_update=True)  # as above
    document = document_service.generate_contract_document(db, contract=contract, actor_id=principal.user_id)
    return DocumentRead.model_validate(document, from_attributes=True)


@router.get("/sales/contracts/{contract_id}/documents", response_model=DocumentPage)
def list_contract_documents(
    contract_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    documents = document_service.list_documents(
        db, tenant_id=principal.tenant_id, owner_type=DocumentOwnerType.CONTRACT, owner_id=contract_id
    )
    return _page(db, tenant_id=principal.tenant_id, documents=list(documents))


@router.get("/sales/documents/{document_id}/pdf")
def download_document_pdf(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Re-renders deterministically from the frozen content_definition on
    every call — never a cached/stored PDF (no blob storage anywhere in
    this codebase).
    """

    document = document_service.get_document_or_404(db, tenant_id=principal.tenant_id, document_id=document_id)
    pdf_bytes = document_service.render_document_pdf(db, document=document, dealership_id=principal.tenant_id)
    return Response(content=pdf_bytes, media_type="application/pdf")


@router.get("/sales/documents/{document_id}/send-options", response_model=SendOptionsRead)
def get_send_options(
    document_id: uuid.UUID,
    principal: Principal = Depends(require_read("customer_send_dialog")),
    db: Session = Depends(get_db),
):
    """KAN-217 PR-6 (FR-A-52/53): what the send dialog shows before a send —
    per channel the recipient, Customers' verdict, whether the channel is
    connected, the text the send would carry and the first refusal the send
    would give. A read: no link, no delivery row, no provider call."""

    options = message_send.send_options(
        db,
        tenant_id=principal.tenant_id,
        group_id=principal.group_id,
        author_user_id=principal.user_id,
        document_id=document_id,
    )
    return SendOptionsRead(
        document_id=options.document_id,
        customer_label=options.customer_label,
        preselected_channel=options.preselected_channel,
        channels=[
            SendChannelOptionRead(
                channel=option.channel,
                ready=option.ready,
                not_ready_reason=option.not_ready_reason,
                sender_address=option.sender_address,
                address=option.address,
                allowed=option.allowed,
                refusal=option.refusal,
                language=option.language,
                text=option.text,
                text_editable=option.text_editable,
                delivery=option.delivery.value,
                blocked=option.blocked,
            )
            for option in options.channels
        ],
    )


@router.post("/sales/documents/{document_id}/send", response_model=MessageDeliveryRead, status_code=201)
def send_document(
    document_id: uuid.UUID,
    body: DocumentSendRequest,
    principal: Principal = Depends(require_write("customer_send_dialog")),
    db: Session = Depends(get_db),
):
    """KAN-217 PR-5 (D-A-41, FR-A-51–54): sends the document to its customer
    on one channel — consent from Customers, the text from the Betrieb's
    library, the sender or provider and the delivery record from integration
    (`services/message_send.py`). 201 with the delivery record, `failed`
    included: the record says what the provider answered."""

    record = message_send.send_document(
        db,
        tenant_id=principal.tenant_id,
        group_id=principal.group_id,
        author_user_id=principal.user_id,
        document_id=document_id,
        channel=body.channel,
        text_override=body.text,
    )
    return MessageDeliveryRead(
        delivery_id=record.delivery_id,
        channel=record.channel.value,
        status=record.status.value,
        sender_address=record.sender_address,
        failure_code=record.failure_code,
        provider_status=record.provider_status,
    )
