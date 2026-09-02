"""IntegrationProvider CRUD (WP-6 PR-1) — platform-staff-maintained
catalogue, same posture as `app.vehicle.services.catalogue_admin`'s own
`Brand` CRUD (versioned, If-Match, platform_admin only at the API layer).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.provider import ProviderCreate, ProviderUpdate


def get_provider_or_404(db: Session, provider_id: uuid.UUID) -> IntegrationProvider:
    provider = db.get(IntegrationProvider, provider_id)
    if provider is None:
        raise NotFoundError(f"Integration provider {provider_id} was not found.")
    return provider


def get_provider_by_code_or_404(db: Session, provider_code: str) -> IntegrationProvider:
    provider = db.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == provider_code))
    if provider is None:
        raise NotFoundError(f"Integration provider '{provider_code}' was not found.")
    return provider


def list_providers(db: Session) -> list[IntegrationProvider]:
    return list(db.scalars(select(IntegrationProvider).order_by(IntegrationProvider.display_name)).all())


def create_provider(db: Session, *, data: ProviderCreate) -> IntegrationProvider:
    existing = db.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == data.provider_code))
    if existing is not None:
        raise ConflictError(f"Provider code '{data.provider_code}' already exists.")
    provider = IntegrationProvider(**data.model_dump())
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def update_provider(db: Session, *, provider: IntegrationProvider, data: ProviderUpdate) -> IntegrationProvider:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(provider, field, value)
    provider.version += 1
    db.commit()
    db.refresh(provider)
    return provider
