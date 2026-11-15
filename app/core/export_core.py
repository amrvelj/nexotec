"""The export declaration of the cross-cutting tables (KAN-218, FR-A-44, Q4 A):
`app/core`'s own models are no context's, so their exporter lives beside them
and the composition root registers it like a context's.

The Betrieb's audit log is in the export (Anto, 2026-11-13, Q4 A): its own
rows (`tenant_id` = the Betrieb) and, since KAN-722 (Anto, 2026-11-15), the
rows it wrote in a group-owned customer's History (`tenant_id` = the group),
attributed by the one rule the History read uses to hide them from its
sisters (`app.core.audit_attribution`) — the exported set is the hidden set.
The customers those rows are about come with them (`references`), so each
entry is readable in the archive on its own. Nothing here rewrites or
deletes it: the deletion ten years after offboarding is a
manual staff step (Anto, 2026-11-14).
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, and_, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.audit_attribution import attributed
from app.core.audit_model import AuditEvent
from app.core.daily_scheduler_model import DailyJobRun
from app.core.export import ExportedTable, ExportScope, TableExporter, table_of, written_at_or_after_xid
from app.core.idempotency_model import IdempotencyRecord
from app.core.outbox_model import OutboxMessage
from app.core.processed_event_model import ProcessedEvent
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun

# Audit rows that record no write of the Betrieb: reads written to the trail
# (a plate history read, a break-glass read, a print) and the export's own
# trail (requesting, acknowledging and downloading an export must not make
# the export it is about out of date).
NON_WRITE_ENTITY_TYPES = frozenset({"vehicle_plate_history", "data_export"})
NON_WRITE_ACTIONS = frozenset({"break_glass_read", "printed", "secret_resolved", "redact"})

_audit = table_of(AuditEvent)


def _group_history_rows(scope: ExportScope) -> ColumnElement[bool]:
    """KAN-722: the rows this Betrieb wrote in a group customer's History —
    the same rows (`entity_type` customer, the group as tenant, the one
    attribution rule) the History read hides from its sisters once it is
    offboarded, so the two sets are equal by construction. The group is
    the Betrieb's own, handed in by platform (`ExportScope.group_id`)."""

    return and_(
        _audit.c.entity_type == "customer",
        _audit.c.tenant_id == scope.group_id,
        attributed(_audit.c, scope.audit_attribution),
    )


def _own_audit_rows(scope: ExportScope) -> ColumnElement[bool]:
    """The Betrieb's own rows, and the rows it wrote in its group's
    customers' History (KAN-722)."""

    return or_(_audit.c.tenant_id == scope.dealership_id, _group_history_rows(scope))


class CoreExporter(TableExporter):
    context = "core"

    def exported_tables(self) -> Sequence[ExportedTable]:
        return (
            ExportedTable(
                table=_audit,
                scope=_own_audit_rows,
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

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]:
        """KAN-722: the group customers whose History holds a row this
        Betrieb wrote — so the exported entry finds its customer in the
        archive (the relationship already includes "the customers its
        history names", `ExportScope.references`)."""

        if scope.audit_attribution.is_empty():
            return {}
        ids = db.scalars(
            select(_audit.c.entity_id).where(_group_history_rows(scope)).distinct()
        )
        return {"customer": frozenset(ids)}

    def last_write_at(self, db: Session, scope: ExportScope) -> dt.datetime | None:
        """An audited write — a deletion leaves no stamp elsewhere, and the
        tables without timestamps (memberships, entitlements) are audited."""

        return db.scalar(select(func.max(AuditEvent.created_at)).where(*_audited_writes(scope)))

    def wrote_outside_snapshot(self, db: Session, scope: ExportScope, xid_floor: int, since: dt.datetime) -> bool:
        """KAN-716: an audited write the export's snapshot may not see."""

        found = db.scalar(
            select(literal(1))
            .select_from(_audit)
            .where(
                *_audited_writes(scope),
                AuditEvent.created_at >= since,
                written_at_or_after_xid(_audit, xid_floor),
            )
            .limit(1)
        )
        return found is not None


def _audited_writes(scope: ExportScope) -> tuple[ColumnElement[bool], ...]:
    return (
        _own_audit_rows(scope),
        AuditEvent.entity_type.not_in(NON_WRITE_ENTITY_TYPES),
        AuditEvent.action.not_in(NON_WRITE_ACTIONS),
    )
