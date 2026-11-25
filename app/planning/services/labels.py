"""KAN-808 PR-1b (ADR-221 (4), T5) — the nightly refresh of a planner
resource's copies of platform's names.

A person resource holds a platform user id with the user's `First Last`
beside it (`user_label`, `user_label_refreshed_at`), and every resource a
platform location id with the location's name (`location_label`,
`location_label_refreshed_at`): rule 2's three-column pattern, validated
through `platform.public` on the write that stores it
(`services/platform_refs.py`). A user or a location renamed later reaches
the planner within a day through the daily jobs `planning.person_labels` and
`planning.location_labels`, read through `app.platform.public` only — never a
join into platform's tables — and committed per batch in this context alone
(ADR-047).

The shared rules of the label refreshes (`.claude/rules/events-worker.md`),
as `contact.advisor_labels` and `inventory.location_labels` apply them:

- no `version` bump: the label is a copy no client writes, and a bump would
  make an open S7 form refuse its next save as stale; a changed label moves
  `updated_at` (what the API returns changed);
- a current label is only restamped, `updated_at` held;
- a deactivated user keeps the name they had (restamped, not rewritten);
- an id naming nothing, or a location of another Betrieb, is left alone —
  label and stamp — and reported; its stale stamp is what the label-age alarm
  shows, and reconciliation names an id naming nothing;
- a deactivated location's name is followed like any other: deactivating
  does not blank it;
- batches in id order, each read FOR NO KEY UPDATE SKIP LOCKED: a resource
  another writer holds is left for the next night, never waited for. The job
  does not take the master-data advisory lock (`services/locks.py`), so it
  never queues behind an S7 write either.

A label longer than the column is cut to it, as the write path cuts it.
A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.planning.models.resource import LABEL_LENGTH, Resource
from app.platform.public import get_location_labels, get_user_display_labels

_BATCH = 500


@dataclass
class RefreshReport:
    # (resource id, the label it had, the label it has now)
    changed: list[tuple[uuid.UUID, str | None, str]] = field(default_factory=list)
    restamped: int = 0
    # (resource id, the platform id it names): an id naming nothing, or another Betrieb's location.
    unresolved: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)


def refresh_person_labels(db: Session, *, commit: Callable[[], None] | None = None) -> RefreshReport:
    """Re-read the name of every person resource's user, one `commit()` per
    batch (the session's own unless the caller passes another). A deactivated
    user's label is kept and restamped."""

    def judge(resource: Resource, labels: dict) -> str | None | _Unresolved:
        assert resource.user_id is not None
        current = labels.get(resource.user_id)
        if current is None:
            return _UNRESOLVED
        if current.deactivated:
            return None
        return current.label

    return _refresh(
        db,
        commit=commit,
        has_id=Resource.user_id.is_not(None),
        platform_id=lambda resource: resource.user_id,
        read=get_user_display_labels,
        judge=judge,
        label_attr="user_label",
        stamp_attr="user_label_refreshed_at",
    )


def refresh_location_labels(db: Session, *, commit: Callable[[], None] | None = None) -> RefreshReport:
    """Re-read the name of every resource's location, one `commit()` per
    batch. A location that is not the resource's own Betrieb's is left alone."""

    def judge(resource: Resource, labels: dict) -> str | None | _Unresolved:
        current = labels.get(resource.location_id)
        if current is None or current.dealership_id != resource.tenant_id:
            return _UNRESOLVED
        return current.label

    return _refresh(
        db,
        commit=commit,
        has_id=Resource.location_id.is_not(None),
        platform_id=lambda resource: resource.location_id,
        read=get_location_labels,
        judge=judge,
        label_attr="location_label",
        stamp_attr="location_label_refreshed_at",
    )


class _Unresolved:
    pass


_UNRESOLVED = _Unresolved()


def _refresh(
    db: Session,
    *,
    commit: Callable[[], None] | None,
    has_id: ColumnElement[bool],
    platform_id: Callable[[Resource], uuid.UUID | None],
    read: Callable[[Session, set[uuid.UUID]], dict],
    judge: Callable[[Resource, dict], str | None | _Unresolved],
    label_attr: str,
    stamp_attr: str,
) -> RefreshReport:
    """`judge` gives the label a resource should hold now, None to keep the
    one it has (only restamped), or `_UNRESOLVED` to leave it alone."""

    finish = commit or db.commit
    report = RefreshReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(Resource)
            .where(has_id)
            .order_by(Resource.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(Resource.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return report
        last_id = batch[-1].id

        labels = read(db, {pid for resource in batch if (pid := platform_id(resource)) is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for resource in batch:
            verdict = judge(resource, labels)
            if isinstance(verdict, _Unresolved):
                pid = platform_id(resource)
                assert pid is not None
                report.unresolved.append((resource.id, pid))
                continue
            held = getattr(resource, label_attr)
            if verdict is None or verdict[:LABEL_LENGTH] == held:
                stamp_only.append(resource.id)
                continue
            report.changed.append((resource.id, held, verdict[:LABEL_LENGTH]))
            setattr(resource, label_attr, verdict[:LABEL_LENGTH])
            setattr(resource, stamp_attr, now)
        # A copy, refreshed on purpose: no version bump.
        db.flush()
        if stamp_only:
            db.execute(
                update(Resource)
                .where(Resource.id.in_(stamp_only))
                .values({stamp_attr: now, "updated_at": Resource.updated_at})
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(stamp_only)
        finish()


def oldest_person_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest user label on a person resource — what the worker
    heartbeat records as `dms.label.age_seconds{label="planning.person"}`
    (CLAUDE.md rule 10). A value well past a day means the job stopped, a
    user id names no user, or a resource was held when the job passed (that
    one clears itself the next night). None when there is no person."""

    return _age(
        db.scalar(
            select(func.min(Resource.user_label_refreshed_at)).where(
                Resource.user_id.is_not(None), Resource.user_label_refreshed_at.is_not(None)
            )
        )
    )


def oldest_location_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest location label on a resource — what the worker
    heartbeat records as `dms.label.age_seconds{label="planning.location"}`.
    A value well past a day means the job stopped, or a location id names no
    location or another Betrieb's. None when there is no resource."""

    return _age(db.scalar(select(func.min(Resource.location_label_refreshed_at))))


def _age(stamp: dt.datetime | None) -> float | None:
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()
