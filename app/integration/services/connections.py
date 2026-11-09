"""IntegrationConnection CRUD, secret CRUD, enable/disable/delete (WP-6
PR-1). This is the ONE place that ties a connection's own row to its
secret refs — never bypassed by a caller reaching for
`secrets_backend` directly outside this module.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
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

# Three vocabularies meet at `integration_entitlement.capability_code`, and
# they are different layers:
#
#   sheet line  One of the identAPI's licence options (ident_basis,
#               ausstattung, bewertung, vin, pneu, energielabel), seeded as the
#               provider's `capability_codes` by migration 6b98739fe743
#               (KAN-488; they replaced the eight SOAP account-sheet counters
#               of 49be490e2077). Entitlement rows are keyed by these; each is
#               probed by its own identAPI call on "Test connection"
#               (`entitlement_probes.py`: 403 = not granted); a line with no
#               row reads as "granted".
#   feature     A name a screen asks about through `tenant_has_capability`.
#               Derived on each ask: `packages` and `images` follow
#               `ausstattung` (FEATURE_SHEET_LINES), optimistically;
#               `vin_decode` follows `vin` (KAN-549, ADR-113) and `valuation`
#               follows `bewertung` (KAN-552) strictly (STRICT_FEATURE_OPTIONS):
#               granted only where a probe recorded the row as granted — no
#               row is NOT granted, the opposite of the optimistic default.
#   call label  What `call_capability(capability=...)` names a block by:
#               `vehicle_data` (catalogue_sync's and identification's umbrella
#               over the Datennamen they call), `system_watermark`,
#               `marketplace_publish`, and a probe's own sheet line. Never an
#               entitlement. Since KAN-486 the call log names each request
#               by the provider's own operation (the Datenname, `FtpUpload`)
#               and keeps the label only for a block that metered none.
#
# tests/architecture/test_capability_codes_are_in_the_vocabulary.py fails when
# a consumer reads a code that is in none of the first two layers.

# A feature whose entitlement is exactly one licence option.
# KAN-488: the identAPI's licence options replace the sheet lines (migration
# 6b98739fe743): `ident_basis`, `ausstattung`, `bewertung`, `vin`, `pneu`,
# `energielabel`. The features screens ask about keep their names and follow
# the option that licenses them: option lists, packages and images are all
# the `ausstattung` option (there is no separate image or package licence).
FEATURE_SHEET_LINES: dict[str, str] = {"packages": "ausstattung", "images": "ausstattung"}

# Features read STRICTLY: granted only where «Test connection» probed the
# option and recorded it as granted — no row is NOT granted. `vin_decode`
# (KAN-549: the test licence has no `vin`, ADR-113, and the rung must not learn
# that from a refused provider call) and `valuation` (KAN-552: the ticket shows
# «Bewertung von auto-i-dat holen» only when the connection's `bewertung` row is
# granted). A connection tested before KAN-552 has no `bewertung` row until it
# is tested again.
STRICT_FEATURE_OPTIONS: dict[str, str] = {"vin_decode": "vin", "valuation": "bewertung"}

# KAN-549 (ADR-113): VIN identification is the identAPI's licence option
# `vin`; KAN-552: valuation is `bewertung`. Both probed on «Test connection»
# like every option and read strictly (STRICT_FEATURE_OPTIONS above).
VIN_LICENCE_OPTION = "vin"
VALUATION_LICENCE_OPTION = "bewertung"


def _validate_required_config(provider: IntegrationProvider, config: dict) -> None:
    missing = [key for key in provider.required_config_keys if not config.get(key)]
    if missing:
        raise UnprocessableEntityError(
            f"Connection config for '{provider.provider_code}' is missing required field(s): {', '.join(missing)}.",
            details={"reason": "missing_config_fields", "missingConfigKeys": missing},
        )


def _connection_query(*, tenant_id: uuid.UUID | None, connection_id: uuid.UUID):
    stmt = select(IntegrationConnection).where(IntegrationConnection.id == connection_id)
    if tenant_id is not None:
        stmt = stmt.where(IntegrationConnection.tenant_id == tenant_id)
    return stmt


def get_connection_or_404(
    db: Session, *, tenant_id: uuid.UUID | None, connection_id: uuid.UUID
) -> IntegrationConnection:
    """`tenant_id=None` means "any tenant" — platform_admin's own reach.
    For a dealer-manager caller, `tenant_id` is always the caller's own
    tenant, which structurally also excludes every platform-scoped row
    (their own `tenant_id` is NULL, and NULL never equals a real UUID) —
    no separate scope check is needed on top of this filter.
    """

    connection = db.scalar(_connection_query(tenant_id=tenant_id, connection_id=connection_id))
    if connection is None:
        raise NotFoundError(f"Connection {connection_id} was not found.")
    return connection


def get_connection_for_update_or_404(
    db: Session, *, tenant_id: uuid.UUID | None, connection_id: uuid.UUID, deleting: bool = False
) -> IntegrationConnection:
    """KAN-429: `get_connection_or_404`, the row locked until the caller
    commits. Every route that checks If-Match against the connection loads
    it this way, so two writes sent with the same version run one after the
    other and the second is refused as stale. The connection is the first
    row those routes lock (a delete then removes its secret refs and
    entitlements), and the secret routes take it the same way before they
    touch a secret ref, so the two never lock in opposite orders. FOR NO KEY
    UPDATE: it serialises the writers. A delete (`deleting`) takes FOR
    UPDATE, the lock its DELETE ends up holding anyway. Until KAN-470 that
    also made a provider call's call-log insert wait for the delete (the
    log's foreign key); the call log has no foreign key now and outlives
    the connection, so no lock here blocks it. A function of its own so the
    read above stays a read
    (tests/architecture/test_adr_047_no_shared_transaction.py)."""

    connection = db.scalar(
        _connection_query(tenant_id=tenant_id, connection_id=connection_id).with_for_update(key_share=not deleting)
    )
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


_UNIQUE_CONNECTION_CONSTRAINT = "uq_integration_connection_tenant_provider_env"


def _duplicate_connection(
    data: ConnectionCreate, provider_code: str, tenant_id: uuid.UUID | None
) -> ConflictError:
    scope = "platform-scoped" if tenant_id is None else "tenant-scoped"
    return ConflictError(
        f"A {scope} {data.environment.value} connection to '{provider_code}' already exists.",
        details={
            "reason": "duplicate_connection",
            "scope": data.scope.value,
            "tenantId": str(tenant_id) if tenant_id is not None else None,
            "providerCode": provider_code,
            "environment": data.environment.value,
        },
    )


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
    else:
        if tenant_id is None:
            raise ConflictError("A tenant-scoped connection requires a tenant.")
        resolved_tenant_id = tenant_id

    # KAN-274: a duplicate is a 409 the client can explain, never the 500
    # an unmapped IntegrityError becomes. Checked explicitly for both
    # scopes — the platform scope has to be, since the unique constraint
    # cannot see two NULL tenant_ids as equal — and the constraint stays
    # the backstop for two tenant-scoped creates racing past this check.
    existing = db.scalar(
        select(IntegrationConnection.id).where(
            IntegrationConnection.scope == data.scope,
            IntegrationConnection.tenant_id.is_(None)
            if resolved_tenant_id is None
            else IntegrationConnection.tenant_id == resolved_tenant_id,
            IntegrationConnection.provider_id == provider.id,
            IntegrationConnection.environment == data.environment,
        )
    )
    if existing is not None:
        raise _duplicate_connection(data, provider.provider_code, resolved_tenant_id)

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
    try:
        with db.begin_nested():
            db.add(connection)
            db.flush()
    except IntegrityError as exc:
        # Only the duplicate is a 409; any other integrity failure stays loud.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != _UNIQUE_CONNECTION_CONSTRAINT:
            raise
        raise _duplicate_connection(data, provider.provider_code, resolved_tenant_id) from exc
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

    The call log is left alone (KAN-470, Anto 2026-10-26, option A): it is
    ADR-024 retention data, kept until `retention.purge_call_log_metadata`
    removes it, and names the connection by id and by the label it
    recorded at the time of each call.
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
    # KAN-488: a removed credential is a credential change like a rotation —
    # `rotated_at` is what the identAPI adapter keys its cached tokens by, so a
    # token obtained with the removed secret stops being used at once.
    connection.rotated_at = utcnow()
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


def record_probed_entitlement(
    db: Session, *, connection_id: uuid.UUID, capability_code: str, granted: bool
) -> IntegrationEntitlement:
    """Upsert the one `(connection, capability)` row as `source=probed`.
    Used by `services/entitlement_probes.py`'s probes via
    `gateway.test_connection` (KAN-38 PR 2b) — what establishes an
    entitlement by observation rather than by declaration.
    """

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


def licence_option_granted(db: Session, *, connection_id: uuid.UUID, option: str) -> bool:
    """A licence option read strictly: the connection's probed row, and
    nothing else. No row is not granted. Writes nothing, so another context's
    request can ask it inside its own transaction (ADR-047)."""

    entitlement = get_entitlement(db, connection_id=connection_id, capability_code=option)
    return entitlement is not None and entitlement.granted


def vin_decode_granted(db: Session, *, connection_id: uuid.UUID) -> bool:
    """KAN-549: may this connection send a VIN query — its `vin` row."""

    return licence_option_granted(db, connection_id=connection_id, option=VIN_LICENCE_OPTION)


def valuation_granted(db: Session, *, connection_id: uuid.UUID) -> bool:
    """KAN-552: may this connection ask for a valuation — its `bewertung` row."""

    return licence_option_granted(db, connection_id=connection_id, option=VALUATION_LICENCE_OPTION)


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

    `vin_decode` (KAN-549) and `valuation` (KAN-552) are deliberate
    exceptions, handled before the generic scan (`STRICT_FEATURE_OPTIONS`):
    each follows its licence option strictly — granted only where an enabled
    connection's probed row says so; "no row" means "not granted", the
    opposite of this function's optimistic bias for every other capability.

    A feature named in `FEATURE_SHEET_LINES` is answered from its sheet
    line: `packages` and `images` read `ausstattung` rows.
    """

    strict_option = STRICT_FEATURE_OPTIONS.get(capability_code)
    if strict_option is not None:
        enabled = db.scalars(
            select(IntegrationConnection.id).where(
                IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.enabled.is_(True)
            )
        ).all()
        return any(
            licence_option_granted(db, connection_id=connection_id, option=strict_option) for connection_id in enabled
        )

    capability_code = FEATURE_SHEET_LINES.get(capability_code, capability_code)

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
