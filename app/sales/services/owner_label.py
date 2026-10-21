"""KAN-404 — the nightly refresh of the responsible seller's label.

`owner_label` on `sales_offer`, `sales_contract` and `sales_deal` is rule 2's
copy of a platform user's `First Last` beside `owner_user_id` (KAN-200),
written when the seller is set or handed over. A user renamed later would
otherwise keep the old name on every open deal and on the Sales grid's
"Verantwortlich" column, for as long as the record lives. This job re-reads the names through `app.platform.public` (no join
into platform's tables) and commits per batch in this context alone
(ADR-047) — the same rules as the customer's advisor label (KAN-403,
`app/customer/services/advisor_label.py`).
"""

import datetime as dt
import uuid
from typing import TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.platform.public import get_user_display_labels
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer

_BATCH = 500
_Owned = TypeVar("_Owned", SalesOffer, SalesContract, SalesDeal)


def refresh_owner_labels(db: Session) -> int:
    """Re-read every responsible seller's name on offers, contracts and deal
    rows; returns how many rows' labels changed. Walks each table in id
    order, one commit per batch.

    A changed label is written through the ORM (updated_at moves: what the API
    returns changed, so an `updatedSince` sync picks it up); `version` is not
    bumped — the label is a copy no client writes, and a bump would make a
    seller's open workspace refuse its next save as stale. A current label,
    and the label of a deactivated user (it keeps the name it had), only get
    `owner_label_refreshed_at` stamped, with updated_at held. An owner id that
    names no user is left untouched; the stale stamp is what the sync-age
    alarm then shows.

    Each batch is read FOR NO KEY UPDATE SKIP LOCKED: a row another writer
    holds is left for the next night, never waited for, so the job can never
    be caught in a lock cycle with a Sales writer (offer → contract → deal)
    or the deactivation step's hand-over.
    """

    # Sales' lock order (offer → contract → deal, KAN-147). Each batch commits
    # before the next, so the job never holds rows of two tables at once.
    offers = _refresh_table(db, SalesOffer)
    contracts = _refresh_table(db, SalesContract)
    deals = _refresh_table(db, SalesDeal)
    return offers + contracts + deals


def _refresh_table(db: Session, model: type[_Owned]) -> int:
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model)
            .where(model.owner_user_id.is_not(None))
            .order_by(model.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(model.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        labels = get_user_display_labels(db, {row.owner_user_id for row in batch if row.owner_user_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.owner_user_id) if row.owner_user_id is not None else None
            if current is None:
                continue
            if current.deactivated or current.label == row.owner_label:
                stamp_only.append(row.id)
                continue
            row.owner_label = current.label
            row.owner_label_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(model)
                .where(model.id.in_(stamp_only))
                .values(owner_label_refreshed_at=now, updated_at=model.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def oldest_owner_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest responsible-seller label across the three tables —
    what the worker heartbeat records as `dms.label.age_seconds{label="sales.owner"}`
    (CLAUDE.md rule 10). The nightly job restamps every row whose seller it
    can resolve and whose row it can lock, so a value well past a day means
    the job has stopped, an owner id names no user, or a row was held by a
    writer when the job passed (that one clears itself the next night). None
    when no row has a seller."""

    stamps = [_oldest_stamp(db, SalesOffer), _oldest_stamp(db, SalesContract), _oldest_stamp(db, SalesDeal)]
    present = [stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=dt.UTC) for stamp in stamps if stamp]
    if not present:
        return None
    return (utcnow() - min(present)).total_seconds()


def _oldest_stamp(db: Session, model: type[_Owned]) -> dt.datetime | None:
    return db.scalar(
        select(func.min(model.owner_label_refreshed_at)).where(
            model.owner_user_id.is_not(None), model.owner_label_refreshed_at.is_not(None)
        )
    )
