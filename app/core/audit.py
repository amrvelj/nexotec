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


# ADR-121 (KAN-564; Anto, 2026-11-07, option B, decision 1 A): the value a
# redacted key carries from then on. The key stays, so the row still says
# which field changed («VIN geändert»).
REDACTED = "[redacted]"


def redact_audit_values(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    keys: frozenset[str],
    actor_id: uuid.UUID | None,
    reason: str,
) -> int:
    """ADR-121: the one sanctioned rewrite of the append-only audit log.

    When a retention purge deletes an entity, the personal values its own
    earlier rows copied are blanked: in `before` and `after`, a key in `keys`
    keeps its name and a non-null value becomes `REDACTED`. No row is
    deleted, reordered or re-dated; `action`, `actor_id`, `reason` and
    `created_at` never change. One `redact` row records the ids of the rows
    touched and the keys, never the values. Never commits: the purge's
    transaction does. Its callers are allow-listed by name
    (`tests/architecture/test_audit_log_redaction_callers.py`).
    """

    rows = db.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == entity_type,
            AuditEvent.entity_id == entity_id,
            AuditEvent.tenant_id == tenant_id if tenant_id is not None else AuditEvent.tenant_id.is_(None),
        )
    ).all()
    touched: list[uuid.UUID] = []
    for row in rows:
        changed = False
        for side in ("before", "after"):
            payload = getattr(row, side)
            if payload and any(key in keys and value is not None for key, value in payload.items()):
                setattr(
                    row,
                    side,
                    {key: (REDACTED if key in keys and value is not None else value) for key, value in payload.items()},
                )
                changed = True
        if changed:
            touched.append(row.id)
    if touched:
        record_audit_event(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            tenant_id=tenant_id,
            action="redact",
            actor_id=actor_id,
            after={"auditEventIds": [str(event_id) for event_id in touched], "keys": sorted(keys)},
            reason=reason,
        )
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
