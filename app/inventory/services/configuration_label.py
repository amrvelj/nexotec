"""KAN-534 — the nightly refresh of Stock's copy of a configuration's label.

`stock_item.configuration_label` (with `configuration_label_refreshed_at`) is
rule 2's copy of the vehicle context's configuration label beside
`configuration_id`, written when a configuration is attached or a pipeline
item created. Since KAN-510, Stock opens the configurator on a pipeline car's
configuration («Konfiguration bearbeiten»), so an edit of make, model or
variant there would otherwise leave the stock list and detail naming the car
by its old label for as long as the item lives. This job re-reads the labels
through `app.vehicle.public` (no join into the vehicle context's tables) and
commits per batch in this context alone (ADR-047) — the same rules as the
customer's vehicle-party labels (KAN-84) and Sales' owner labels (KAN-404).
A refreshed copy is not a repair (.claude/rules/events.md).
"""

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.inventory.models.stock_item import StockItem
from app.vehicle.public import get_configuration_labels

_BATCH = 500


def refresh_configuration_labels(db: Session) -> int:
    """Re-read the label of every stock item's configuration; returns how many
    items' labels changed. Walks the items in id order, one commit per batch.

    A changed label is written through the ORM (updated_at moves: what the API
    returns changed, so an `updatedSince` sync picks it up); `version` is not
    bumped — the label is a copy no client writes, and a bump would make an
    open stock detail refuse its next save as stale. A current label is only
    restamped, with updated_at held. A configuration id that names nothing, or
    a configuration of another Betrieb (reconciliation reports both), is left
    untouched; its stale stamp is what the sync-age alarm then shows.

    Each batch is read FOR NO KEY UPDATE SKIP LOCKED: an item another writer
    holds is left for the next night, never waited for.
    """

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(StockItem)
            .where(StockItem.configuration_id.is_not(None))
            .order_by(StockItem.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(StockItem.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        labels = get_configuration_labels(db, {item.configuration_id for item in batch if item.configuration_id})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for item in batch:
            current = labels.get(item.configuration_id) if item.configuration_id is not None else None
            if current is None or current.tenant_id != item.tenant_id:
                continue
            if current.label == item.configuration_label:
                stamp_only.append(item.id)
                continue
            item.configuration_label = current.label
            item.configuration_label_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(StockItem)
                .where(StockItem.id.in_(stamp_only))
                .values(configuration_label_refreshed_at=now, updated_at=StockItem.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def oldest_configuration_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest configuration label on a stock item — what the
    worker heartbeat records as `dms.label.age_seconds{label="inventory.configuration"}`
    (CLAUDE.md rule 10). None when no item has a configuration with a stamp."""

    stamp = db.scalar(
        select(func.min(StockItem.configuration_label_refreshed_at)).where(
            StockItem.configuration_id.is_not(None), StockItem.configuration_label_refreshed_at.is_not(None)
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()
