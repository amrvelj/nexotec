"""Document generation/download endpoints (WP-8 PR-7). One shared router
for both owner types — offers and contracts render through the exact same
shared block vocabulary and the exact same render_document call.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.permissions import require_write
from app.db import get_db
from app.sales.models.document import DocumentOwnerType
from app.sales.schemas.document import DocumentPage, DocumentRead
from app.sales.services import contract as contract_service
from app.sales.services import document as document_service
from app.sales.services import offer as offer_service

router = APIRouter(tags=["sales"])


@router.post("/sales/offers/{offer_id}/documents", response_model=DocumentRead, status_code=201)
def generate_offer_document(
    offer_id: uuid.UUID,
    principal: Principal = Depends(require_write("sales_offers")),
    db: Session = Depends(get_db),
):
    offer = offer_service.get_offer_or_404(db, principal.tenant_id, offer_id)
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
    return DocumentPage(items=[DocumentRead.model_validate(d, from_attributes=True) for d in documents])


@router.post("/sales/contracts/{contract_id}/documents", response_model=DocumentRead, status_code=201)
def generate_contract_document(
    contract_id: uuid.UUID,
    principal: Principal = Depends(require_write("sales_contracts")),
    db: Session = Depends(get_db),
):
    contract = contract_service.get_contract_or_404(db, principal.tenant_id, contract_id)
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
    return DocumentPage(items=[DocumentRead.model_validate(d, from_attributes=True) for d in documents])


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
