"""IntegrationConnection CRUD, secret CRUD, enable/disable/delete (WP-6
PR-1). This is the ONE place that ties a connection's own row to its
secret refs — never bypassed by a caller reaching for
`secrets_backend` directly outside this module.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.integration.models.connection import ConnectionScope, ConnectionStatus, IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.schemas.connection import ConnectionCreate, ConnectionUpdate
from app.integration.services import providers as provider_service
from app.integration.services import secrets_backend
from app.integration.services.secrets_backend import SecretsBackendNotConfigured

_ENTITY_TYPE = "integration_connection"


def get_connection_or_404(
    db: Session, *, tenant_id: uuid.UUID | None, connection_id: uuid.UUID
) -> IntegrationConnection:
    """`tenant_id=None` means "any tenant" — platform_admin's own reach.
    For a dealer-manager caller, `tenant_id` is always the caller's own
    tenant, which structurally also excludes every platform-scoped row
    (their own `tenant_id` is NULL, and NULL never equals a real UUID) —
    no separate scope check is needed on top of this filter.
    """

    stmt = select(IntegrationConnection).where(IntegrationConnection.id == connection_id)
    if tenant_id is not None:
        stmt = stmt.where(IntegrationConnection.tenant_id == tenant_id)
    connection = db.scalar(stmt)
    if connection is None:
        raise NotFoundError(f"Connection {connection_id} was not found.")
    return connection


def get_enabled_connection(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str
) -> IntegrationConnection | None:
    """The lookup `app.vehicle` (PR-4) uses, via `app.integration.public`,
    to find the connection its own catalogue-sync job should call through
    — never a direct query against `IntegrationConnection`/
    `IntegrationProvider`, which the import-linter blocks from outside
    this context anyway. Returns `None` for a tenant with no such
    connection at all, or one that exists but is disabled — a dealer with
    no provider contract keeps a fully usable module (PR-5); this
    function is exactly the seam that "no connection" fact flows through.
    """

    return db.scalar(
        select(IntegrationConnection)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationProvider.provider_code == provider_code,
            IntegrationConnection.enabled.is_(True),
        )
    )


def list_connections(
    db: Session,
    *,
    tenant_id: uuid.UUID | None,
    provider_code: str | None,
    category: str | None,
    status: ConnectionStatus | None,
    params: SortPageParams,
) -> tuple[list[IntegrationConnection], str | None, int, bool]:
    stmt = select(IntegrationConnection)
    if tenant_id is not None:
        stmt = stmt.where(IntegrationConnection.tenant_id == tenant_id)
    if provider_code is not None or category is not None:
        stmt = stmt.join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        if provider_code is not None:
            stmt = stmt.where(IntegrationProvider.provider_code == provider_code)
        if category is not None:
            stmt = stmt.where(IntegrationProvider.category == category)
    if status is not None:
        stmt = stmt.where(IntegrationConnection.status == status)

    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=IntegrationConnection, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate


def create_connection(
    db: Session, *, tenant_id: uuid.UUID | None, data: ConnectionCreate, actor_id: uuid.UUID | None
) -> IntegrationConnection:
    """`tenant_id` is the caller's own tenant (None only for a
    platform_admin caller creating a platform-scoped connection) — never
    trusted from `data.scope` alone; the API layer is what decides whether
    `data.scope` is honoured (platform_admin) or overridden to `tenant`
    (everyone else), per rule 7's "never a flag someone can flip".
    """

    provider = provider_service.get_provider_or_404(db, data.provider_id)

    if data.scope == ConnectionScope.PLATFORM:
        resolved_tenant_id = None
        # The DB's own unique constraint can't catch two NULL-tenant_id
        # duplicates (NULL never equals NULL in SQL) — checked explicitly
        # here instead, the one case that constraint structurally misses.
        existing = db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.scope == ConnectionScope.PLATFORM,
                IntegrationConnection.provider_id == provider.id,
                IntegrationConnection.environment == data.environment,
            )
        )
        if existing is not None:
            raise ConflictError(
                f"A platform-scoped {data.environment.value} connection to '{provider.provider_code}' already exists."
            )
    else:
        if tenant_id is None:
            raise ConflictError("A tenant-scoped connection requires a tenant.")
        resolved_tenant_id = tenant_id

    connection = IntegrationConnection(
        provider_id=provider.id,
        scope=data.scope,
        tenant_id=resolved_tenant_id,
        display_name=data.display_name,
        environment=data.environment,
        config=data.config,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(connection)
    db.flush()
    record_audit_event(
        db,
        entity_type=_ENTITY_TYPE,
        entity_id=connection.id,
        tenant_id=resolved_tenant_id,
        action="create",
        actor_id=actor_id,
        after={"providerCode": provider.provider_code, "environment": data.environment.value},
    )
    db.commit()
    db.refresh(connection)
    return connection


def update_connection(
    db: Session, *, connection: IntegrationConnection, data: ConnectionUpdate, actor_id: uuid.UUID | None
) -> IntegrationConnection:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(connection, field, value)
    connection.updated_by = actor_id
    connection.version += 1
    db.commit()
    db.refresh(connection)
    return connection


def disable_connection(db: Session, *, connection: IntegrationConnection, actor_id: uuid.UUID | None) -> IntegrationConnection:
    """Instant, reversible (rule 10) — a plain status flip, no cascading
    side effects and no confirmation required, unlike delete below.
    """

    connection.enabled = False
    connection.status = ConnectionStatus.DISABLED
    connection.updated_by = actor_id
    connection.version += 1
    db.flush()
    record_audit_event(
        db, entity_type=_ENTITY_TYPE, entity_id=connection.id, tenant_id=connection.tenant_id,
        action="disable", actor_id=actor_id,
    )
    db.commit()
    db.refresh(connection)
    return connection


def enable_connection(db: Session, *, connection: IntegrationConnection, actor_id: uuid.UUID | None) -> IntegrationConnection:
    connection.enabled = True
    # Reverts to not_configured, not connected — the next successful
    # /test (PR-2) is what earns "connected" back; re-enabling alone
    # proves nothing about whether the credentials still work.
    connection.status = ConnectionStatus.NOT_CONFIGURED
    connection.updated_by = actor_id
    connection.version += 1
    db.flush()
    record_audit_event(
        db, entity_type=_ENTITY_TYPE, entity_id=connection.id, tenant_id=connection.tenant_id,
        action="enable", actor_id=actor_id,
    )
    db.commit()
    db.refresh(connection)
    return connection


def delete_connection(
    db: Session,
    *,
    connection: IntegrationConnection,
    confirm: bool,
    actor_id: uuid.UUID | None,
    secrets_backend_module=secrets_backend,
) -> None:
    """Requires confirmation and is audit-logged (rule 10) — unlike
    disable, this is destructive: every secret slot is deleted from
    Infisical too, not just this row.
    """

    if not confirm:
        raise ConflictError("Deleting a connection requires confirmation.")

    secret_refs = list(
        db.scalars(select(IntegrationSecretRef).where(IntegrationSecretRef.connection_id == connection.id))
    )
    for ref in secret_refs:
        try:
            secrets_backend_module.delete_secret(connection_id=connection.id, slot=ref.slot.value)
        except SecretsBackendNotConfigured:
            pass  # no live Infisical project configured (every test run, most local dev)
        db.delete(ref)
    db.execute(delete(IntegrationEntitlement).where(IntegrationEntitlement.connection_id == connection.id))

    record_audit_event(
        db, entity_type=_ENTITY_TYPE, entity_id=connection.id, tenant_id=connection.tenant_id,
        action="delete", actor_id=actor_id,
    )
    db.delete(connection)
    db.commit()


def set_secret(
    db: Session,
    *,
    connection: IntegrationConnection,
    slot: SecretSlot,
    value: str,
    actor_id: uuid.UUID | None,
    secrets_backend_module=secrets_backend,
) -> IntegrationSecretRef:
    """Upsert = create-or-rotate. Never returns or logs `value` itself —
    only the resulting pointer.
    """

    existing = db.scalar(
        select(IntegrationSecretRef).where(
            IntegrationSecretRef.connection_id == connection.id, IntegrationSecretRef.slot == slot
        )
    )
    if existing is not None:
        secret_ref = secrets_backend_module.update_secret(connection_id=connection.id, slot=slot.value, value=value)
        existing.secret_ref = secret_ref
        existing.rotated_at = utcnow()
        row = existing
        action = "rotate_secret"
    else:
        secret_ref = secrets_backend_module.create_secret(connection_id=connection.id, slot=slot.value, value=value)
        row = IntegrationSecretRef(connection_id=connection.id, slot=slot, secret_ref=secret_ref)
        db.add(row)
        action = "create_secret"

    connection.rotated_at = utcnow()
    db.flush()
    record_audit_event(
        db, entity_type="integration_secret_ref", entity_id=row.id, tenant_id=connection.tenant_id,
        action=action, actor_id=actor_id, reason=f"slot={slot.value}",
    )
    db.commit()
    db.refresh(row)
    return row


def remove_secret(
    db: Session,
    *,
    connection: IntegrationConnection,
    slot: SecretSlot,
    actor_id: uuid.UUID | None,
    secrets_backend_module=secrets_backend,
) -> None:
    existing = db.scalar(
        select(IntegrationSecretRef).where(
            IntegrationSecretRef.connection_id == connection.id, IntegrationSecretRef.slot == slot
        )
    )
    if existing is None:
        raise NotFoundError(f"Connection {connection.id} has no '{slot.value}' secret.")
    secrets_backend_module.delete_secret(connection_id=connection.id, slot=slot.value)
    record_audit_event(
        db, entity_type="integration_secret_ref", entity_id=existing.id, tenant_id=connection.tenant_id,
        action="delete_secret", actor_id=actor_id, reason=f"slot={slot.value}",
    )
    db.delete(existing)
    db.commit()


def list_secret_slots(db: Session, *, connection_id: uuid.UUID) -> list[IntegrationSecretRef]:
    return list(
        db.scalars(
            select(IntegrationSecretRef).where(IntegrationSecretRef.connection_id == connection_id)
        ).all()
    )


def list_entitlements(db: Session, *, connection_id: uuid.UUID) -> list[IntegrationEntitlement]:
    return list(
        db.scalars(
            select(IntegrationEntitlement).where(IntegrationEntitlement.connection_id == connection_id)
        ).all()
    )
