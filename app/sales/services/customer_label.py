"""KAN-590 — the nightly refresh of the customer's label on Sales' open work.

`customer_label` (and, on offers and contracts, `customer_language`) beside
`customer_id` on `sales_offer`, `sales_contract` and `sales_deal` is rule 2's
copy of the customer, written when the buyer is attached
(`offer.apply_customer_change`, the contract's own attach) and when a merge
moves the row (`customer_merge.py`, KAN-580). A customer renamed later kept
the old name on the Sales list, the offer and contract headers and the next
generated document's addressee. This job re-reads the label through
`app.customer.public.get_customer_sales_labels` (no join into the customer
context's tables) and commits per batch in this context alone (ADR-047) —
the rules of `sales.owner_labels` (KAN-404, `owner_label.py`).

**Which rows follow** (Anto, 2026-11-10, ruling 1A): open work only — a
draft or open offer, a pending contract, and the deal rows showing one of
them. A confirmed, invoiced or cancelled contract keeps the name the buyer
signed under, and so does its deal row; a cancelled offer keeps its name
too. Documents already generated are frozen and never touched.
`customer_locality` is left as it is (ruling 2, revised): no path writes it.
"""

import datetime as dt
import uuid
from typing import Any, TypeVar

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.customer.public import get_customer_sales_labels
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer

_BATCH = 500
_Labelled = TypeVar("_Labelled", SalesOffer, SalesContract, SalesDeal)

_OPEN_OFFER = (OfferStatus.DRAFT, OfferStatus.OPEN)
_OPEN_CONTRACT = (ContractStatus.PENDING,)


def _follows(model: type[_Labelled]) -> Any:
    """The rows whose customer label follows the customer: open work only."""

    if model is SalesOffer:
        return SalesOffer.status.in_(_OPEN_OFFER)
    if model is SalesContract:
        return SalesContract.status.in_(_OPEN_CONTRACT)
    # A deal row shows its contract once one exists (`entity_type` flips),
    # with that entity's status as a string (deal_projection.py).
    return or_(
        and_(SalesDeal.entity_type == "offer", SalesDeal.status.in_([s.value for s in _OPEN_OFFER])),
        and_(SalesDeal.entity_type == "contract", SalesDeal.status.in_([s.value for s in _OPEN_CONTRACT])),
    )


def refresh_customer_labels(db: Session) -> int:
    """Re-read the customer's label (and language) on every open offer,
    pending contract and their deal rows; returns how many rows changed.
    Walks each table in id order, one commit per batch.

    A changed label is written through the ORM (updated_at moves: what the API
    returns changed); `version` is not bumped — the label is a copy no client
    writes, and a bump would make a seller's open workspace refuse its next
    save as stale. A current label only gets `customer_denorm_refreshed_at`
    stamped, with updated_at held. A customer id that names no customer, or a
    merged customer (Sales' merge consumer moves those rows, KAN-185), is left
    untouched; the stale stamp is what the sync-age alarm then shows.

    Each batch is read FOR NO KEY UPDATE SKIP LOCKED: a row another writer
    holds is left for the next night, never waited for, so the job can never
    be caught in a lock cycle with a Sales writer (offer → contract → deal),
    the merge consumer or the deactivation step's hand-over.
    """

    # Sales' lock order (offer → contract → deal, KAN-147). Each batch commits
    # before the next, so the job never holds rows of two tables at once.
    offers = _refresh_table(db, SalesOffer)
    contracts = _refresh_table(db, SalesContract)
    deals = _refresh_table(db, SalesDeal)
    return offers + contracts + deals


def _refresh_table(db: Session, model: type[_Labelled]) -> int:
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model)
            .where(model.customer_id.is_not(None), _follows(model))
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

        labels = get_customer_sales_labels(db, {row.customer_id for row in batch if row.customer_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.customer_id) if row.customer_id is not None else None
            if current is None or current.merged:
                continue
            # A deal row carries no language (deal_projection copies the label only).
            same_language = isinstance(row, SalesDeal) or row.customer_language == current.language
            if current.label == row.customer_label and same_language:
                stamp_only.append(row.id)
                continue
            row.customer_label = current.label
            if not isinstance(row, SalesDeal):
                row.customer_language = current.language
            row.customer_denorm_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(model)
                .where(model.id.in_(stamp_only))
                .values(customer_denorm_refreshed_at=now, updated_at=model.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def oldest_customer_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest customer label on open work across the three
    tables — what the worker heartbeat records as
    `dms.label.age_seconds{label="sales.customer"}` (CLAUDE.md rule 10). Only
    rows the job follows count: a signed contract keeps its name on purpose
    and is never restamped. A value well past a day means the job has stopped,
    a customer id names no customer or a merged one whose rows the merge
    consumer has not moved, or a row was held by a writer when the job passed
    (that one clears itself the next night). None when no open row names a
    customer."""

    stamps = [_oldest_stamp(db, SalesOffer), _oldest_stamp(db, SalesContract), _oldest_stamp(db, SalesDeal)]
    present = [stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=dt.UTC) for stamp in stamps if stamp]
    if not present:
        return None
    return (utcnow() - min(present)).total_seconds()


def _oldest_stamp(db: Session, model: type[_Labelled]) -> dt.datetime | None:
    return db.scalar(
        select(func.min(model.customer_denorm_refreshed_at)).where(
            model.customer_id.is_not(None), model.customer_denorm_refreshed_at.is_not(None), _follows(model)
        )
    )
