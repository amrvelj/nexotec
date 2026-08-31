"""IntegrationConnection endpoints (WP-6 PR-1) — the dealer-facing and
platform-facing surface. Gated by the `integration_connections` capability
(manager-flag-only, exact mirror of `dealership_settings`) rather than any
functional AccessRole — the spec is explicit: "visible only to users with
the dealer manager flag." A platform_admin caller always passes
`require_read`/`require_write` too (that dependency's own first check),
and is the only caller who may see across tenants or create a
platform-scoped connection.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.permissions import require_read, require_write
from app.core.sorting import SortField, parse_sort
from app.db import get_db
from app.integration.models.connection import ConnectionScope, ConnectionStatus, IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.schemas.connection import (
    ConnectionCreate,
    ConnectionPage,
    ConnectionRead,
    ConnectionUpdate,
    DeleteConnectionRequest,
    EntitlementRead,
    SecretSlotRead,
    SecretWriteRequest,
)
from app.integration.services import connections as connection_service
from app.integration.services import providers as provider_service

router = APIRouter(tags=["integrations"])
settings = get_settings()

CONNECTION_SORT_FIELDS: dict[str, object] = {
    "displayName": IntegrationConnection.display_name,
    "updatedAt": IntegrationConnection.updated_at,
    "createdAt": IntegrationConnection.created_at,
}
_DEFAULT_CONNECTION_SORT = [
    SortField(api_name="updatedAt", column=IntegrationConnection.updated_at, direction="desc", nullable=False)
]


def _is_platform_admin(principal: Principal) -> bool:
    return AccessRole.PLATFORM_ADMIN in principal.roles


def _connection_read(db: Session, connection: IntegrationConnection) -> ConnectionRead:
    # Not model_validate(connection) + model_copy(update={...}): providerCode
    # is a required field on ConnectionRead but has no matching attribute
    # on the ORM object, so the initial validation pass would fail before
    # ever reaching the copy. Building the full dict up front instead.
    provider = provider_service.get_provider_or_404(db, connection.provider_id)
    secret_slots = connection_service.list_secret_slots(db, connection_id=connection.id)
    entitlements = connection_service.list_entitlements(db, connection_id=connection.id)
    return ConnectionRead(
        id=connection.id,
        provider_id=connection.provider_id,
        provider_code=provider.provider_code,
        scope=connection.scope,
        tenant_id=connection.tenant_id,
        display_name=connection.display_name,
        environment=connection.environment,
        config=connection.config,
        enabled=connection.enabled,
        status=connection.status,
        last_verified_at=connection.last_verified_at,
        last_error=connection.last_error,
        expires_at=connection.expires_at,
        rotated_at=connection.rotated_at,
        secret_slots=[SecretSlotRead.model_validate(s, from_attributes=True) for s in secret_slots],
        entitlements=[EntitlementRead.model_validate(e, from_attributes=True) for e in entitlements],
        version=connection.version,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


@router.get("/integrations/connections", response_model=ConnectionPage)
def list_connections(
    tenant_id: uuid.UUID | None = Query(default=None),
    provider_code: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: ConnectionStatus | None = Query(default=None),
    sort: str | None = Query(default=None),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(require_read("integration_connections")),
    db: Session = Depends(get_db),
):
    # Non-platform_admin: always own tenant, the `tenant_id` query param
    # (if any) is ignored — never let a manager widen their own read by
    # supplying someone else's tenant_id.
    effective_tenant_id = tenant_id if _is_platform_admin(principal) else principal.tenant_id
    sort_fields = parse_sort(sort, allowed=CONNECTION_SORT_FIELDS) or _DEFAULT_CONNECTION_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = connection_service.list_connections(
        db, tenant_id=effective_tenant_id, provider_code=provider_code, category=category, status=status, params=params
    )
    return ConnectionPage(
        items=[_connection_read(db, r) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/integrations/connections/{connection_id}", response_model=ConnectionRead)
def get_connection(
    connection_id: uuid.UUID,
    principal: Principal = Depends(require_read("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    return _connection_read(db, connection)


@router.post("/integrations/connections", response_model=ConnectionRead, status_code=201)
def create_connection(
    body: ConnectionCreate,
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    # Never trust `body.scope` from a non-platform_admin caller (rule 7:
    # "never a flag someone can flip") — force it to tenant regardless of
    # what the request body says.
    data = body if _is_platform_admin(principal) else body.model_copy(update={"scope": ConnectionScope.TENANT})
    connection = connection_service.create_connection(
        db, tenant_id=principal.tenant_id, data=data, actor_id=principal.user_id
    )
    return _connection_read(db, connection)


@router.patch("/integrations/connections/{connection_id}", response_model=ConnectionRead)
def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    check_version(connection.version, if_match, entity_name="IntegrationConnection")
    connection = connection_service.update_connection(db, connection=connection, data=body, actor_id=principal.user_id)
    return _connection_read(db, connection)


@router.post("/integrations/connections/{connection_id}/disable", response_model=ConnectionRead)
def disable_connection(
    connection_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    check_version(connection.version, if_match, entity_name="IntegrationConnection")
    connection = connection_service.disable_connection(db, connection=connection, actor_id=principal.user_id)
    return _connection_read(db, connection)


@router.post("/integrations/connections/{connection_id}/enable", response_model=ConnectionRead)
def enable_connection(
    connection_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    check_version(connection.version, if_match, entity_name="IntegrationConnection")
    connection = connection_service.enable_connection(db, connection=connection, actor_id=principal.user_id)
    return _connection_read(db, connection)


@router.delete("/integrations/connections/{connection_id}", status_code=204)
def delete_connection(
    connection_id: uuid.UUID,
    body: DeleteConnectionRequest,
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    connection_service.delete_connection(db, connection=connection, confirm=body.confirm, actor_id=principal.user_id)


@router.put("/integrations/connections/{connection_id}/secrets/{slot}", response_model=SecretSlotRead)
def set_secret(
    connection_id: uuid.UUID,
    slot: SecretSlot,
    body: SecretWriteRequest,
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    """Write-only, always — the response never carries `value`, only the
    slot's own metadata (rule 2: rotation replaces, it never reads).
    """

    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    ref = connection_service.set_secret(
        db, connection=connection, slot=slot, value=body.secret_value, actor_id=principal.user_id
    )
    return SecretSlotRead.model_validate(ref, from_attributes=True)


@router.delete("/integrations/connections/{connection_id}/secrets/{slot}", status_code=204)
def remove_secret(
    connection_id: uuid.UUID,
    slot: SecretSlot,
    principal: Principal = Depends(require_write("integration_connections")),
    db: Session = Depends(get_db),
):
    tenant_id = None if _is_platform_admin(principal) else principal.tenant_id
    connection = connection_service.get_connection_or_404(db, tenant_id=tenant_id, connection_id=connection_id)
    connection_service.remove_secret(db, connection=connection, slot=slot, actor_id=principal.user_id)
