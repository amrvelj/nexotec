"""KAN-739 — a stock item's copy of its location's name (rule 2's three-column pattern).

`stock_item.location_id` is platform's Location.id; `location_label` (with
`location_label_refreshed_at`) is the name beside it, what the Stock grid's
«Standort» shows and sorts and filters by (ADR-060). Read only through
`app.platform.public.get_location_labels` — never a join into platform's
tables.

- **On write** (`location_label_for`): whenever a create or a PATCH sets the
  id, the label is read and stamped in the same transaction. An id that names
  no location of the item's own Betrieb is 422 `unknown_location`, the
  integration screen's refusal (KAN-222): another Betrieb's site is refused
  without confirming it exists, and its name never lands on this Betrieb's row
  (rule 7).
- **Nightly** (`refresh_location_labels`, the daily job
  `inventory.location_labels`): a location renamed in Dealer Administration
  reaches the grid within a day. The shared rules of the label refreshes
  (`.claude/rules/events-worker.md`): no `version` bump, a current label only
  restamped, an id naming nothing or another Betrieb's location left alone
  (its stale stamp is what the label-age alarm shows; reconciliation reports
  an id naming nothing), a deactivated location keeps its name, batches
  `FOR NO KEY UPDATE SKIP LOCKED`. An item with an id and no label yet (a row
  written before KAN-739) is filled the same way — the backfill script is
  this job run once, dry-run first.

A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.inventory.models.stock_item import StockItem
from app.platform.public import get_location_labels

_BATCH = 500


def location_label_for(db: Session, *, tenant_id: uuid.UUID, location_id: uuid.UUID | None) -> str | None:
    """The name to store beside `location_id` on an item of `tenant_id`, or
    None for no location. 422 `unknown_location` for an id that is not one of
    this Betrieb's locations."""

    if location_id is None:
        return None
    current = get_location_labels(db, {location_id}).get(location_id)
    if current is None or current.dealership_id != tenant_id:
        raise UnprocessableEntityError(
            "The location is not one of this Betrieb's.",
            details={"reason": "unknown_location", "field": "locationId", "locationId": str(location_id)},
        )
    return current.label


def set_location(db: Session, item: StockItem, location_id: uuid.UUID | None) -> None:
    """Point `item` at `location_id` with its name beside it (create and PATCH)."""

    item.location_label = location_label_for(db, tenant_id=item.tenant_id, location_id=location_id)
    item.location_id = location_id
    item.location_label_refreshed_at = utcnow() if location_id is not None else None


@dataclass
class RefreshReport:
    changed: list[tuple[uuid.UUID, str | None, str]] = field(default_factory=list)
    restamped: int = 0
    # An id naming no location, or another Betrieb's: left alone.
    unresolved: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)


def refresh_location_labels(db: Session, *, commit: Callable[[], None] | None = None) -> RefreshReport:
    """Re-read the name of every located item's location, in id order, one
    `commit()` per batch (the session's own commit unless the caller passes
    another — the backfill's dry run passes a flush). Each batch is read FOR
    NO KEY UPDATE SKIP LOCKED: an item another writer holds is left for the
    next night, never waited for."""

    finish = commit or db.commit
    report = RefreshReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(StockItem)
            .where(StockItem.location_id.is_not(None))
            .order_by(StockItem.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(StockItem.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return report
        last_id = batch[-1].id

        labels = get_location_labels(db, {item.location_id for item in batch if item.location_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for item in batch:
            assert item.location_id is not None
            current = labels.get(item.location_id)
            if current is None or current.dealership_id != item.tenant_id:
                report.unresolved.append((item.id, item.location_id))
                continue
            if current.label == item.location_label:
                stamp_only.append(item.id)
                continue
            report.changed.append((item.id, item.location_label, current.label))
            item.location_label = current.label
            item.location_label_refreshed_at = now
        # A copy, refreshed on purpose: no version bump (no client writes it,
        # and a bump would make an open detail refuse its next save as stale).
        db.flush()
        if stamp_only:
            db.execute(
                update(StockItem)
                .where(StockItem.id.in_(stamp_only))
                .values(location_label_refreshed_at=now, updated_at=StockItem.updated_at)
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(stamp_only)
        finish()


def oldest_location_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest location label on a stock item — what the worker
    heartbeat records as `dms.label.age_seconds{label="inventory.location"}`
    (CLAUDE.md rule 10). None when no located item has a stamp."""

    stamp = db.scalar(
        select(func.min(StockItem.location_label_refreshed_at)).where(
            StockItem.location_id.is_not(None),
            StockItem.location_label_refreshed_at.is_not(None),
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()
