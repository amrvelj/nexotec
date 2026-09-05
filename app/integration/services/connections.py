"""IntegrationConnection CRUD, secret CRUD, enable/disable/delete (WP-6
PR-1). This is the ONE place that ties a connection's own row to its
secret refs — never bypassed by a caller reaching for
`secrets_backend` directly outside this module.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionScope, ConnectionStatus, IntegrationConnection
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot
from app.integration.schemas.connection import ConnectionCreate, ConnectionUpdate
from app.integration.services import providers as provider_service
from app.integration.services import secrets_backend
from app.integration.services.secrets_backend import SecretsBackendNotConfigured

_ENTITY_TYPE = "integration_connection"

# KAN-36 — vin_decode is never hand-declared: it's derived from the health
# of the tenant's own `dat` connection (the DAT sub-account auto-i-dat
# issues alongside the main account, which is what actually entitles VIN
# decode). Handled as a special case in tenant_has_capability below rather
# than the plain per-connection entitlement scan, because "no row for this
# capability" must mean "not granted" here — the opposite of that
# function's own optimistic default for every other, declared capability.
_VIN_DECODE_CAPABILITY = "vin_decode"
_DAT_PROVIDER_CODE = "dat"
_AUTO_I_DAT_PROVIDER_CODE = "auto_i_dat"


def _validate_required_config(provider: IntegrationProvider, config: dict) -> None:
    missing = [key for key in provider.required_config_keys if not config.get(key)]
    if missing:
        raise UnprocessableEntityError(
            f"Connection config for '{provider.provider_code}' is missing required field(s): {', '.join(missing)}.",
            details={"missingConfigKeys": missing},
        )


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


def list_enabled_connection_tenant_ids_for_provider(db: Session, *, provider_code: str) -> list[uuid.UUID]:
    """The daily-job composition root's own enumeration (PR-4) — "which
    tenants have an enabled connection to this provider_code" — so
    `app/integration/daily_jobs.py` never queries `IntegrationConnection`/
    `IntegrationProvider` directly (it lives outside this context) and
    never needs to. Platform-scope rows (`tenant_id IS NULL`) are excluded
    by construction: `IntegrationConnection.tenant_id.is_not(None)` isn't
    even needed here since a NULL never appears in a `.distinct()` list
    the caller then iterates as real tenant ids — included for clarity
    anyway.
    """

    stmt = (
        select(IntegrationConnection.tenant_id)
        .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
        .where(
            IntegrationProvider.provider_code == provider_code,
            IntegrationConnection.enabled.is_(True),
            IntegrationConnection.tenant_id.is_not(None),
        )
        .distinct()
    )
    return [tenant_id for tenant_id in db.scalars(stmt).all() if tenant_id is not None]


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
    _validate_required_config(provider, data.config)

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
    if data.config is not None:
        provider = provider_service.get_provider_or_404(db, connection.provider_id)
        _validate_required_config(provider, data.config)
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


def get_entitlement(
    db: Session, *, connection_id: uuid.UUID, capability_code: str
) -> IntegrationEntitlement | None:
    """The single-capability lookup PR-5's degradation logic uses via
    `app.integration.public` — `None` means "never probed or declared",
    a state the caller (app.vehicle.services.catalogue_entitlements)
    interprets, never this module: whether "unknown" defaults to granted
    or not is a policy decision for whoever is degrading, not for the
    registry that just stores what it's been told.
    """

    return db.scalar(
        select(IntegrationEntitlement).where(
            IntegrationEntitlement.connection_id == connection_id,
            IntegrationEntitlement.capability_code == capability_code,
        )
    )


def _upsert_entitlement(
    db: Session, *, connection_id: uuid.UUID, capability_code: str, granted: bool
) -> IntegrationEntitlement:
    existing = get_entitlement(db, connection_id=connection_id, capability_code=capability_code)
    now = utcnow()
    if existing is not None:
        existing.granted = granted
        existing.source = EntitlementSource.PROBED
        existing.checked_at = now
        row = existing
    else:
        row = IntegrationEntitlement(
            connection_id=connection_id, capability_code=capability_code, granted=granted,
            source=EntitlementSource.PROBED, checked_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def compute_vin_decode_entitlement(db: Session, *, tenant_id: uuid.UUID) -> bool:
    """KAN-36 — derived, never hand-declared: granted exactly when this
    tenant holds a healthy (`enabled` + `CONNECTED`) `dat` connection, the
    DAT sub-account that actually entitles VIN decode (reached through the
    tenant's own auto-i-dat account, per the account sheet's own VIN/
    VINIdentDB counters). Recomputed on every call rather than cached
    behind write-side triggers — the same "derived on read, never stored,
    never repaired by a nightly job" posture this codebase already applies
    to valuation status (ADR-066). When the tenant also holds an enabled
    `auto_i_dat` connection, the freshly computed value is upserted onto
    it as a `source=probed` `integration_entitlement` row purely so it's
    visible on that connection's own entitlements list — that row is a
    cache of the last time this was asked, never a second source of truth;
    the return value here always is.
    """

    dat_connection = get_enabled_connection(db, tenant_id=tenant_id, provider_code=_DAT_PROVIDER_CODE)
    granted = dat_connection is not None and dat_connection.status == ConnectionStatus.CONNECTED

    auto_i_dat_connection = get_enabled_connection(db, tenant_id=tenant_id, provider_code=_AUTO_I_DAT_PROVIDER_CODE)
    if auto_i_dat_connection is not None:
        _upsert_entitlement(
            db, connection_id=auto_i_dat_connection.id, capability_code=_VIN_DECODE_CAPABILITY, granted=granted
        )
    return granted


def tenant_has_capability(db: Session, *, tenant_id: uuid.UUID, capability_code: str) -> bool:
    """The generic degradation check any screen can ask — "can THIS
    tenant currently do X" — without needing to know which provider or
    which connection backs it. Optimistic-default across every enabled
    connection the tenant holds: granted unless a connection's own
    entitlement row explicitly says otherwise, never granted at all with
    no enabled connection. This duplicates `app.vehicle.services.
    catalogue_entitlements`'s own policy deliberately (per this module's
    `get_entitlement` docstring: the registry stays neutral, each
    consuming context — here, PR-7's Sales/Valuation screens via this
    dedicated capability endpoint — owns its own degradation policy over
    the same neutral data).

    `vin_decode` (KAN-36) is a deliberate exception, handled before the
    generic scan: it isn't declared on any one connection, it's derived
    across two, and "no row anywhere" must mean "not granted" — the
    opposite of this function's own optimistic bias for every other
    capability.
    """

    if capability_code == _VIN_DECODE_CAPABILITY:
        return compute_vin_decode_entitlement(db, tenant_id=tenant_id)

    connections = db.scalars(
        select(IntegrationConnection).where(
            IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.enabled.is_(True)
        )
    ).all()
    if not connections:
        return False
    for connection in connections:
        entitlement = get_entitlement(db, connection_id=connection.id, capability_code=capability_code)
        if entitlement is not None and not entitlement.granted:
            return False
    return True


_USAGE_PERIOD_DAYS = 30


def get_usage(db: Session, *, connection: IntegrationConnection) -> tuple[int, Decimal | None]:
    """PR-7's own "View usage" action — calls and cost-units over a
    trailing 30-day window, from `integration_call_log` directly (never a
    materialized/cached count, since a dealer checking this occasionally
    doesn't need one). Indicative only (I-2/I-3): Nexotec never bills on
    top of a provider's own contract, and the schema this feeds
    (`UsageRead`) carries `indicative=True` for exactly that reason.
    """

    since = utcnow() - dt.timedelta(days=_USAGE_PERIOD_DAYS)
    calls = db.scalar(
        select(func.count()).select_from(IntegrationCallLog).where(
            IntegrationCallLog.connection_id == connection.id, IntegrationCallLog.created_at >= since
        )
    ) or 0
    cost_units = db.scalar(
        select(func.sum(IntegrationCallLog.cost_units)).where(
            IntegrationCallLog.connection_id == connection.id, IntegrationCallLog.created_at >= since
        )
    )
    return calls, (Decimal(cost_units) if cost_units is not None else None)
