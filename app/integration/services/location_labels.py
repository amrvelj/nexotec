"""KAN-222 — the nightly refresh of a connection's copy of its location's name.

`integration_connection.location_label` (with `location_label_refreshed_at`)
is rule 2's copy of the platform context's location name beside
`location_id`, written when the manager binds the connection (FR-A-21). A
location renamed in Dealer Administration afterwards would otherwise leave
the card naming the site by its old name. This job re-reads the names through
`app.platform.public` (no join into platform's tables) and commits in this
context alone (ADR-047) — the rules of the other label refreshes
(`.claude/rules/events-worker.md`): no `version` bump (no client writes the
copy, and a bump would make an open card refuse its next save as stale), a
current label only restamped, an id naming nothing or another Betrieb's
location left alone (its stale stamp is what the label-age alarm shows, and
reconciliation reports an id naming nothing). A deactivated location keeps
its name: it is still the site the connection was bound to.

A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.models.connection import IntegrationConnection
from app.platform.public import get_location_labels

_BATCH = 500


def refresh_location_labels(db: Session) -> int:
    """Re-read the name of every bound connection's location; returns how many
    labels changed. Walks the connections in id order, one commit per batch,
    each batch read FOR NO KEY UPDATE SKIP LOCKED: a connection another
    writer holds is left for the next night, never waited for."""

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(IntegrationConnection)
            .where(IntegrationConnection.location_id.is_not(None))
            .order_by(IntegrationConnection.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(IntegrationConnection.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        labels = get_location_labels(db, {c.location_id for c in batch if c.location_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for connection in batch:
            current = labels.get(connection.location_id) if connection.location_id is not None else None
            if current is None or current.dealership_id != connection.tenant_id:
                continue
            if current.label == connection.location_label:
                stamp_only.append(connection.id)
                continue
            connection.location_label = current.label
            connection.location_label_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(IntegrationConnection)
                .where(IntegrationConnection.id.in_(stamp_only))
                .values(location_label_refreshed_at=now, updated_at=IntegrationConnection.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def oldest_location_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest location label on a connection — what the worker
    heartbeat records as `dms.label.age_seconds{label="integration.location"}`
    (CLAUDE.md rule 10). None when no bound connection has a stamp."""

    stamp = db.scalar(
        select(func.min(IntegrationConnection.location_label_refreshed_at)).where(
            IntegrationConnection.location_id.is_not(None),
            IntegrationConnection.location_label_refreshed_at.is_not(None),
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()
