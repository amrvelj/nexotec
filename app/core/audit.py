import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.pagination import PageParams, build_page, paginate_query


def record_audit_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    action: str,
    actor_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        tenant_id=tenant_id,
        action=action,
        actor_id=actor_id,
        before=before,
        after=after,
        reason=reason,
    )
    db.add(event)
    db.flush()
    return event


REDACTED = "[redacted]"


def redact_audit_values(
    db: Session, *, entity_type: str, entity_id: uuid.UUID, tenant_id: uuid.UUID | None,
    keys: frozenset[str], actor_id: uuid.UUID | None, reason: str,
) -> int:
    """ADR-121: the one sanctioned rewrite of the append-only audit log.
    Blanks `keys` in the before/after of this entity's rows (the key stays,
    a non-null value becomes REDACTED); no row is deleted, no other column
    changes. Records one `redact` row naming the touched rows and the keys,
    never the values. Never commits: the purge's transaction does."""
    rows = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id,
        AuditEvent.tenant_id == tenant_id)).all()
    touched: list[uuid.UUID] = []
    for row in rows:
        changed = False
        for side in ("before", "after"):
            payload = getattr(row, side)
            if payload and keys & payload.keys():
                setattr(row, side, {k: (REDACTED if k in keys and v is not None else v) for k, v in payload.items()})
                changed = True
        if changed:
            touched.append(row.id)
    if touched:
        record_audit_event(db, entity_type=entity_type, entity_id=entity_id, tenant_id=tenant_id,
            action="redact", actor_id=actor_id, reason=reason,
            after={"auditEventIds": [str(i) for i in touched], "keys": sorted(keys)})
    db.flush()
    return len(touched)


def list_audit_events(
    db: Session, *, entity_type: str, entity_id: uuid.UUID, tenant_id: uuid.UUID | None
) -> list[AuditEvent]:
    """tenant_id=None means "any tenant" (platform_admin / global entities
    like Vehicle); callers must apply their own authorization check for that
    case rather than relying on this filter.
    """

    stmt = select(AuditEvent).where(
        AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id
    )
    if tenant_id is not None:
        stmt = stmt.where(AuditEvent.tenant_id == tenant_id)
    stmt = stmt.order_by(AuditEvent.created_at.asc())
    return list(db.scalars(stmt).all())


def list_tenant_audit_events(
    db: Session, *, tenant_id: uuid.UUID, params: PageParams
) -> tuple[list[AuditEvent], str | None]:
    """Full audit trail for a tenant across every entity type — the shape
    GET /v1/dealers/{id}/audit-log needs, since the spec lists one audit-log
    endpoint per Dealership rather than a separate one per sub-entity (User).
    """

    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    stmt = paginate_query(stmt, model=AuditEvent, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)
