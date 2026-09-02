"""IntegrationProvider endpoints (WP-6 PR-1) — the catalogue every
connection points at. Read: any authenticated user (a dealer manager
needs to see what's available to connect to). Write: platform_admin only,
same gate as app.vehicle.api.catalogue_admin's own Brand CRUD.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.db import get_db
from app.integration.schemas.provider import ProviderCreate, ProviderPage, ProviderRead, ProviderUpdate
from app.integration.services import providers as provider_service

router = APIRouter(tags=["integrations"])


@router.get("/integrations/providers", response_model=ProviderPage)
def list_providers(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = provider_service.list_providers(db)
    return ProviderPage(items=[ProviderRead.model_validate(p, from_attributes=True) for p in rows])


@router.post("/integrations/providers", response_model=ProviderRead, status_code=201)
def create_provider(
    body: ProviderCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    provider = provider_service.create_provider(db, data=body)
    return ProviderRead.model_validate(provider, from_attributes=True)


@router.patch("/integrations/providers/{provider_id}", response_model=ProviderRead)
def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    provider = provider_service.get_provider_or_404(db, provider_id)
    check_version(provider.version, if_match, entity_name="IntegrationProvider")
    provider = provider_service.update_provider(db, provider=provider, data=body)
    return ProviderRead.model_validate(provider, from_attributes=True)
