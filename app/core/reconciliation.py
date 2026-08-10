"""Generic nightly-reconciliation framework (P-10) — the compensating
control for PR-2 dropping the nine cross-context foreign keys. Each
bounded context declares its own list of ReferenceChecks (see
app.customer.reconciliation, app.vehicle.reconciliation,
app.sales.reconciliation) describing its outbound cross-context
references; this module runs them, persists every finding, and raises if
any are found. It never deletes or repairs anything — detection only.

Read-only by construction: every check is a SELECT. Nothing in this module
issues an UPDATE or DELETE against the tables it inspects.
"""

import dataclasses
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Session

from app.core.base import utcnow
from app.core.reconciliation_model import ReconciliationOrphan, ReconciliationRun


@dataclasses.dataclass(frozen=True)
class ReferenceCheck:
    """Describes one outbound cross-context reference to verify still
    resolves: source_model.source_fk_column must either be NULL (only
    permitted when nullable=True) or match some row's target_id_column on
    target_model.
    """

    label: str
    source_model: type[DeclarativeBase]
    source_row_id_column: InstrumentedAttribute[Any]
    source_fk_column: InstrumentedAttribute[Any]
    target_model: type[DeclarativeBase]
    target_id_column: InstrumentedAttribute[Any]
    nullable: bool = False


def find_orphans(db: Session, check: ReferenceCheck) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Read-only: rows in source_model whose source_fk_column doesn't
    resolve to any row in target_model. Returns (source_row_id, dangling_value)
    pairs. NULL source_fk_column values are never orphans — they're absent
    references, not dangling ones — so nullable checks explicitly exclude them.
    """

    stmt = (
        select(check.source_row_id_column, check.source_fk_column)
        .select_from(check.source_model)
        .outerjoin(check.target_model, check.source_fk_column == check.target_id_column)
        .where(check.target_id_column.is_(None))
    )
    if check.nullable:
        stmt = stmt.where(check.source_fk_column.is_not(None))
    return list(db.execute(stmt).all())


class ReconciliationAlarm(Exception):
    """Raised after a run's findings are persisted, if any orphans were
    found. The durable record in reconciliation_run / reconciliation_orphan
    survives regardless of whether anything catches this — persist first,
    alarm second.
    """

    def __init__(self, run: ReconciliationRun, orphans: list[ReconciliationOrphan]) -> None:
        self.run = run
        self.orphans = orphans
        super().__init__(
            f"{run.context}: {len(orphans)} orphaned cross-context reference(s) found "
            f"— see reconciliation_orphan for run {run.id}."
        )


def run_reconciliation(db: Session, *, context: str, checks: list[ReferenceCheck]) -> ReconciliationRun:
    """Runs every check for one context, persists a ReconciliationRun plus
    one ReconciliationOrphan per finding, commits, then raises
    ReconciliationAlarm if anything was found. Never deletes or repairs —
    detection only (P-10).
    """

    run = ReconciliationRun(context=context, started_at=utcnow(), checks_run=len(checks), orphans_found=0)
    db.add(run)
    db.flush()

    orphans: list[ReconciliationOrphan] = []
    for check in checks:
        for source_row_id, dangling_value in find_orphans(db, check):
            orphan = ReconciliationOrphan(
                run_id=run.id,
                context=context,
                check_label=check.label,
                source_table=check.source_model.__tablename__,
                source_row_id=source_row_id,
                target_table=check.target_model.__tablename__,
                dangling_value=dangling_value,
                detected_at=utcnow(),
            )
            db.add(orphan)
            orphans.append(orphan)

    run.finished_at = utcnow()
    run.orphans_found = len(orphans)
    db.commit()
    db.refresh(run)

    if orphans:
        raise ReconciliationAlarm(run, orphans)
    return run
