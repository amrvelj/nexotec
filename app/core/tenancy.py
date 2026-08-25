"""Tenant-scoped lookups shared by every entity router.

Cross-tenant isolation rule: looking up a record that exists but belongs to
another tenant must behave identically to it not existing at all — 404, not
403. A 403 would leak the fact that the ID is valid in someone else's tenant.
"""

import uuid
from typing import TypeVar

from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal
from app.core.errors import NotFoundError

ModelT = TypeVar("ModelT")


def get_or_404(db: Session, model: type[ModelT], entity_id: uuid.UUID, tenant_id: uuid.UUID) -> ModelT:
    obj = (
        db.query(model)
        .filter(model.id == entity_id, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
        .one_or_none()
    )
    if obj is None:
        raise NotFoundError(f"{model.__name__} {entity_id} was not found.")
    return obj


def require_tenant_match(resource_tenant_id: uuid.UUID, principal: Principal) -> None:
    """For entities that *are* the tenant (Dealership has no tenant_id column
    of its own to filter on via get_or_404) — the caller must be
    platform_admin or the dealership's own principal. Cross-tenant access
    still 404s, never 403s.
    """

    if AccessRole.PLATFORM_ADMIN not in principal.roles and principal.tenant_id != resource_tenant_id:
        raise NotFoundError(f"Dealership {resource_tenant_id} was not found.")
