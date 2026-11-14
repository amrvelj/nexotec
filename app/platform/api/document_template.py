"""DocumentTemplate CRUD (WP-6b PR-2, ADR-044 tier 2).

Nested under /dealerships/{dealership_id}, same shape as
/dealerships/{id}/users — the caller must be platform_admin or the
dealership's own principal (require_tenant_match, 404-not-403 on a
cross-tenant path id), and require_read/require_write("document_templates")
gates who among that principal's own dealership may act at all (any role
reads, only platform_admin or the manager flag writes).
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.object_store import ObjectStore, get_object_store
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.schemas.document_content import DocumentWatermark
from app.platform.schemas.document_template import DocumentTemplateRead, DocumentTemplateUpdate
from app.platform.services import document_template as document_template_service
from app.platform.services.dealership import get_dealership_or_404
from app.platform.services.document_render import render_document
from app.platform.services.document_template_preview import PreviewDocument, sample_content

router = APIRouter(tags=["document-templates"])


@router.get("/dealerships/{dealership_id}/document-template", response_model=DocumentTemplateRead)
def get_document_template(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("document_templates")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    return document_template_service.get_document_template_or_default(db, dealership_id)


@router.patch("/dealerships/{dealership_id}/document-template", response_model=DocumentTemplateRead)
def update_document_template(
    dealership_id: uuid.UUID,
    body: DocumentTemplateUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("document_templates")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    template = document_template_service.upsert_document_template(
        db,
        dealership_id=dealership_id,
        data=body,
        if_match_version=if_match,
        actor_id=principal.user_id,
    )
    return DocumentTemplateRead.model_validate(template, from_attributes=True)


@router.get(
    "/dealerships/{dealership_id}/document-template/preview",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The sample document as a PDF."}},
)
def preview_document_template(
    dealership_id: uuid.UUID,
    document: PreviewDocument = Query(default=PreviewDocument.OFFER, description="The sample to print."),
    location: uuid.UUID | None = Query(
        default=None, description="Print it for this site: its address block and logo, its own bank account."
    ),
    principal: Principal = Depends(require_write("document_templates")),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """KAN-206 (FR-A-06): a sample offer or invoice as it prints today — a
    PDF from the one renderer (ADR-051, the same `render_document` every
    document goes through), with the saved template, the Betrieb's master
    data and «MUSTER» across the page, in the Betrieb's correspondence
    language. Only those who may edit the template may call it: the footer
    prints the Betrieb's real bank account. Another Betrieb's id or site is a
    404 (rule 7)."""

    require_tenant_match(dealership_id, principal)
    dealership = get_dealership_or_404(db, dealership_id)
    language = dealership.default_correspondence_language
    pdf = render_document(
        db,
        dealership_id=dealership_id,
        correspondence_language=language,
        content=sample_content(document, language=language, signer_user_id=principal.user_id),
        location_id=location,
        store=store,
        watermark=DocumentWatermark.SAMPLE,
    )
    return Response(content=pdf, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})
