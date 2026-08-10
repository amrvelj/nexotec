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
    endpoint per Dealer rather than a separate one per sub-entity (User).
    """

    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    stmt = paginate_query(stmt, model=AuditEvent, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)
