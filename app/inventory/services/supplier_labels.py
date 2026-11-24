"""KAN-465 — a stock item's copy of its supplier contact's name (rule 2's three-column pattern).

`stock_item.supplier_contact_id` is the contact context's Contact.id (a
contact with the supplier role); `supplier_label` (with
`supplier_label_refreshed_at`) is the name beside it, what the Stock grid's
«Lieferant» shows, sorts and filters by and the detail's purchase card names.
Read only through `app.contact.public` — never a join into the contact
context's tables.

- **On booking** (`services/purchase.py::record_purchase`): the label is read
  and stamped in the booking's transaction.
- **Nightly** (`refresh_supplier_labels`, the daily job
  `inventory.supplier_labels`): a contact renamed in Kontakte reaches the grid
  within a day. The shared rules of the label refreshes
  (`.claude/rules/events-worker.md`): no `version` bump, a current label only
  restamped, an id naming nothing left alone (its stale stamp is what the
  label-age alarm shows; reconciliation reports an id naming nothing), a
  merged contact left as it is (`repoint_merged_supplier` moves it), batches
  `FOR NO KEY UPDATE SKIP LOCKED`. A booking from before KAN-465 has a label
  and no id: it keeps the name typed then (Anto, 2026-11-21, option A).
- **On a contact merge** (`repoint_merged_supplier`, the consumer
  `inventory.contact_merged`): the items naming the duplicate move to the
  survivor with the survivor's name, as Sales' rows do (KAN-185).

A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.contact.public import contact_display_name, get_contact_or_404, get_contact_sales_labels
from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.inventory.models.stock_item import StockItem

_BATCH = 500


@dataclass
class RefreshReport:
    changed: list[tuple[uuid.UUID, str | None, str]] = field(default_factory=list)
    restamped: int = 0
    # An id naming no contact, or a merged one: left alone.
    unresolved: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)


def refresh_supplier_labels(db: Session, *, commit: Callable[[], None] | None = None) -> RefreshReport:
    """Re-read the name of every booked item's supplier contact, in id order,
    one `commit()` per batch. Each batch is read FOR NO KEY UPDATE SKIP
    LOCKED: an item another writer holds is left for the next night."""

    finish = commit or db.commit
    report = RefreshReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(StockItem)
            .where(StockItem.supplier_contact_id.is_not(None))
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

        labels = get_contact_sales_labels(
            db, {item.supplier_contact_id for item in batch if item.supplier_contact_id is not None}
        )
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for item in batch:
            assert item.supplier_contact_id is not None
            current = labels.get(item.supplier_contact_id)
            if current is None or current.merged:
                report.unresolved.append((item.id, item.supplier_contact_id))
                continue
            if current.label == item.supplier_label:
                stamp_only.append(item.id)
                continue
            report.changed.append((item.id, item.supplier_label, current.label))
            item.supplier_label = current.label
            item.supplier_label_refreshed_at = now
        # A copy, refreshed on purpose: no version bump (no client writes it,
        # and a bump would make an open detail refuse its next save as stale).
        db.flush()
        if stamp_only:
            db.execute(
                update(StockItem)
                .where(StockItem.id.in_(stamp_only))
                .values(supplier_label_refreshed_at=now, updated_at=StockItem.updated_at)
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(stamp_only)
        finish()


def oldest_supplier_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest supplier label on a stock item — what the worker
    heartbeat records as `dms.label.age_seconds{label="inventory.supplier"}`
    (CLAUDE.md rule 10). None when no item names a supplier contact."""

    stamp = db.scalar(
        select(func.min(StockItem.supplier_label_refreshed_at)).where(
            StockItem.supplier_contact_id.is_not(None),
            StockItem.supplier_label_refreshed_at.is_not(None),
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()


def repoint_merged_supplier(
    db: Session, *, group_id: uuid.UUID, duplicate_id: uuid.UUID, target_id: uuid.UUID
) -> int:
    """Move every item whose supplier is the merged `duplicate_id` to
    `target_id` (the end of its merge chain, which the caller resolved), with
    the survivor's name read from its owner now — never from the payload.
    A survivor the group cannot read moves the id and keeps the label (the
    nightly job relabels it once it can). No version bump: a copy, as the
    nightly job. Flushes only; the consumer's harness commits. Returns how
    many items moved."""

    items = list(
        db.scalars(
            select(StockItem)
            .where(StockItem.supplier_contact_id == duplicate_id)
            .order_by(StockItem.id)
            .with_for_update(key_share=True)
        ).all()
    )
    if not items:
        return 0
    try:
        label: str | None = contact_display_name(get_contact_or_404(db, group_id, target_id))
    except NotFoundError:
        label = None
    now = utcnow()
    for item in items:
        item.supplier_contact_id = target_id
        if label is not None:
            item.supplier_label = label
            item.supplier_label_refreshed_at = now
    db.flush()
    return len(items)
