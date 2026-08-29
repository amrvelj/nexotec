"""DocumentTemplate CRUD (WP-6b PR-2, ADR-044 tier 2).

Nested under /dealerships/{dealership_id}, same shape as
/dealerships/{id}/users — the caller must be platform_admin or the
dealership's own principal (require_tenant_match, 404-not-403 on a
cross-tenant path id), and require_read/require_write("document_templates")
gates who among that principal's own dealership may act at all (any role
reads, only platform_admin or the manager flag writes).
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.concurrency import require_if_match
from app.core.permissions import require_read, require_write
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.schemas.document_template import DocumentTemplateRead, DocumentTemplateUpdate
from app.platform.services import document_template as document_template_service

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
