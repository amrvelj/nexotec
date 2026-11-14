"""The export declaration of the cross-cutting tables (KAN-218, FR-A-44, Q4 A):
`app/core`'s own models are no context's, so their exporter lives beside them
and the composition root registers it like a context's.

The Betrieb's audit log is in the export (Anto, 2026-11-13, Q4 A). At the
deletion after the retention period its rows stay — the audit log is
append-only — and their values are blanked through the one sanctioned rewrite
(`redact_audit_values`, ADR-121): every key of every row of the Betrieb's
entities, one `redact` row per entity, never a deleted row.
"""

import datetime as dt
import uuid
from collections.abc import Callable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import REDACTED, redact_audit_values
from app.core.audit_model import AuditEvent
from app.core.daily_scheduler_model import DailyJobRun
from app.core.export import ErasureResult, ExportedTable, ExportScope, TableExporter, table_of
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun

ERASURE_REASON = "dealership_retention_expired"

# Audit rows that record no write of the Betrieb: reads written to the trail
# (a plate history read, a break-glass read, a print) and the export's own
# trail (requesting, acknowledging and downloading an export must not make
# the export it is about out of date).
NON_WRITE_ENTITY_TYPES = frozenset({"vehicle_plate_history", "data_export"})
NON_WRITE_ACTIONS = frozenset({"break_glass_read", "printed", "secret_resolved", "redact"})

_audit = table_of(AuditEvent)


def _own_audit_rows(scope: ExportScope):
    return _audit.c.tenant_id == scope.dealership_id


class CoreExporter(TableExporter):
    context = "core"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return (
            ExportedTable(
                table=_audit,
                scope=_own_audit_rows,
                erase=False,
                keep_reason="The audit log is append-only (ADR-121): its values are redacted, its rows stay.",
                # Judged in last_write_at below, without the reads and the export's own trail.
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        return {
            IdempotencyRecord.__tablename__: "A retry key kept 24 hours (KAN-119) — transport, not a record.",
            OutboxMessage.__tablename__: "The event transport; each fact is in the owning context's tables.",
            ProcessedEvent.__tablename__: "Consumer bookkeeping, no Betrieb's data.",
            DailyJobRun.__tablename__: "Nexotec's job scheduler, no Betrieb's data.",
            ReconciliationRun.__tablename__: "Nexotec's nightly integrity check, no Betrieb's data.",
            ReconciliationOrphan.__tablename__: "Nexotec's nightly integrity check, no Betrieb's data.",
        }

    def last_write_at(self, db: Session, scope: ExportScope) -> dt.datetime | None:
        """An audited write — a deletion leaves no stamp elsewhere, and the
        tables without timestamps (memberships, entitlements) are audited."""

        return db.scalar(
            select(func.max(AuditEvent.created_at)).where(
                AuditEvent.tenant_id == scope.dealership_id,
                AuditEvent.entity_type.not_in(NON_WRITE_ENTITY_TYPES),
                AuditEvent.action.not_in(NON_WRITE_ACTIONS),
            )
        )

    def erase(self, session_factory: Callable[[], Session], scope: ExportScope) -> ErasureResult:
        """Redacts the Betrieb's audit values entity by entity, one commit
        per entity, so a run that stops resumes where it stopped: an entity
        whose values are all redacted already is skipped. The Betrieb's
        transport rows (idempotency records, delivered events) are deleted."""

        db = session_factory()
        touched = 0
        try:
            entities = db.execute(
                select(AuditEvent.entity_type, AuditEvent.entity_id)
                .where(AuditEvent.tenant_id == scope.dealership_id, AuditEvent.action != "redact")
                .distinct()
            ).all()
            for entity_type, entity_id in entities:
                keys = _unredacted_keys(db, scope.dealership_id, entity_type, entity_id)
                if not keys:
                    continue
                touched += redact_audit_values(
                    db,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    tenant_id=scope.dealership_id,
                    keys=frozenset(keys),
                    actor_id=None,
                    reason=ERASURE_REASON,
                )
                db.commit()
            # No harness purge covers these: the idempotency purge takes HTTP records only, none purges outbox rows.
            for model in (IdempotencyRecord, OutboxMessage):
                rows = db.query(model).filter(model.tenant_id == scope.dealership_id)
                if model is OutboxMessage:
                    rows = rows.filter(OutboxMessage.published_at.is_not(None))
                touched += rows.delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
        return ErasureResult(context=self.context, rows_deleted=touched)


def _unredacted_keys(db: Session, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID) -> set[str]:
    keys: set[str] = set()
    for before, after in db.execute(
        select(AuditEvent.before, AuditEvent.after).where(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.entity_type == entity_type,
            AuditEvent.entity_id == entity_id,
            AuditEvent.action != "redact",
        )
    ):
        for payload in (before, after):
            for key, value in (payload or {}).items():
                if value is not None and value != REDACTED:
                    keys.add(key)
    return keys
