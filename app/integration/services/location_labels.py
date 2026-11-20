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

KAN-226: a Google Business Profile location binding carries the same copy and
is refreshed by the same job, under the same rules; a disconnected binding is
history and keeps the name it had.

A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid
from typing import cast

from sqlalchemy import ColumnElement, func, select, true, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.models.connection import IntegrationConnection
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding
from app.platform.public import get_location_labels

_BATCH = 500

_Labelled = type[IntegrationConnection] | type[GbpLocationBinding]


def _in_scope(model: _Labelled) -> ColumnElement[bool]:
    if model is GbpLocationBinding:
        return GbpLocationBinding.status != GbpBindingStatus.DISCONNECTED
    return true()


def _refresh(db: Session, model: _Labelled) -> int:
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model)
            .where(model.location_id.is_not(None), _in_scope(model))
            .order_by(model.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(model.id > last_id)
        batch = cast(list[IntegrationConnection | GbpLocationBinding], list(db.scalars(stmt).all()))
        if not batch:
            return changed
        last_id = batch[-1].id

        labels = get_location_labels(db, {row.location_id for row in batch if row.location_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.location_id) if row.location_id is not None else None
            if current is None or current.dealership_id != row.tenant_id:
                continue
            if current.label == row.location_label:
                stamp_only.append(row.id)
                continue
            row.location_label = current.label
            row.location_label_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(model)
                .where(model.id.in_(stamp_only))
                .values(location_label_refreshed_at=now, updated_at=model.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def refresh_location_labels(db: Session) -> int:
    """Re-read the name of every bound connection's and every live Business
    Profile binding's location; returns how many labels changed. Walks each
    table in id order, one commit per batch, each batch read FOR NO KEY UPDATE
    SKIP LOCKED: a row another writer holds is left for the next night, never
    waited for."""

    return _refresh(db, IntegrationConnection) + _refresh(db, GbpLocationBinding)


def _oldest(db: Session, model: _Labelled) -> dt.datetime | None:
    return db.scalar(
        select(func.min(model.location_label_refreshed_at)).where(
            model.location_id.is_not(None), model.location_label_refreshed_at.is_not(None), _in_scope(model)
        )
    )


def oldest_location_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest location label on a connection or a live Business
    Profile binding — what the worker heartbeat records as
    `dms.label.age_seconds{label="integration.location"}` (CLAUDE.md rule 10).
    None when no bound row has a stamp."""

    stamps = [stamp for model in (IntegrationConnection, GbpLocationBinding) if (stamp := _oldest(db, model))]
    if not stamps:
        return None
    stamp = min(s if s.tzinfo is not None else s.replace(tzinfo=dt.UTC) for s in stamps)
    return (utcnow() - stamp).total_seconds()
