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
draft or open offer no live contract was made from, a pending contract, and
the deal rows showing one of them. A confirmed, invoiced or cancelled
contract keeps the name the buyer signed under, and so does its deal row; a
cancelled offer keeps its name too. Rows that do not follow are only
restamped («checked, kept on purpose»), as `sales.owner_labels` restamps
every row it can resolve, so a row that comes back into open work (the offer
a cancelled contract frees, a copied offer) never brings a stale stamp.

**An offer with a live contract mirrors that contract** (KAN-408: the two
never disagree): after the contract pass, such an offer takes its contract's
committed label and language — the new name while the contract is pending,
the signed name once it is signed. Never the customer's directly: a contract
a writer held when the job passed, or one signed between two passes, would
otherwise leave the two apart for good. Deal rows follow their live entity
the same way. Documents already generated are frozen and never touched.
`customer_locality` is left as it is (ruling 2, revised): no path fills it.

The correspondence language follows with the label (the plan Anto approved
on 2026-11-10 re-reads "label, language and locality"; locality was then
dropped). For a merged customer's open work this means: the merge consumer
moves the rows and leaves their language (KAN-580), and the next night this
job gives them the survivor's language, so the next document prints in the
survivor's language. Signed contracts keep theirs.
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
from app.sales.services.ownership import offer_is_open

_BATCH = 500
_Labelled = TypeVar("_Labelled", SalesOffer, SalesContract, SalesDeal)

_OPEN_OFFER = (OfferStatus.DRAFT, OfferStatus.OPEN)
_OPEN_CONTRACT = (ContractStatus.PENDING,)


def _follows(model: type[_Labelled]) -> Any:
    """The rows whose customer label follows the customer directly: open work
    without a live contract above it.

    An offer follows while it is draft or open and no live (not cancelled)
    contract was made from it (`ownership.offer_is_open`, Sales' one
    definition). An offer keeps its status when a contract is made from it;
    such an offer mirrors its contract instead (`_mirror_offers_to_contracts`).
    A cancelled contract frees its offer, which follows again."""

    if model is SalesOffer:
        return offer_is_open()
    if model is SalesContract:
        return SalesContract.status.in_(_OPEN_CONTRACT)
    # A deal row shows its contract once one exists (`entity_type` flips),
    # with that entity's status as a string (deal_projection.py).
    return or_(
        and_(SalesDeal.entity_type == "offer", SalesDeal.status.in_([s.value for s in _OPEN_OFFER])),
        and_(SalesDeal.entity_type == "contract", SalesDeal.status.in_([s.value for s in _OPEN_CONTRACT])),
    )


def refresh_customer_labels(db: Session) -> int:
    """Re-read the customer's label (and language) on open work — an offer
    no signed contract was made from, a pending contract, and their deal
    rows; returns how many rows changed. Signed work (and a cancelled offer)
    keeps its label and is only restamped, as `sales.owner_labels` restamps
    every row it can resolve. Walks each table in id order, one commit per
    batch.

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
    # Offers again, after the contracts committed: an offer with a live
    # contract takes that contract's label. It locks offer rows only and reads
    # the contracts unlocked, so the lock order holds.
    mirrored = _mirror_offers_to_contracts(db)
    deals = _refresh_table(db, SalesDeal)
    return offers + contracts + mirrored + deals


def _mirror_offers_to_contracts(db: Session) -> int:
    """Give each draft or open offer with a live contract that contract's
    committed `customer_label` and `customer_language`; returns how many
    offers changed. Same batch, lock and stamp rules as `_refresh_table`."""

    live_contract = (
        select(SalesContract.id)
        .where(SalesContract.offer_id == SalesOffer.id, SalesContract.status != ContractStatus.CANCELLED)
        .correlate(SalesOffer)
        .exists()
    )
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(SalesOffer)
            .where(SalesOffer.status.in_(_OPEN_OFFER), live_contract)
            .order_by(SalesOffer.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(SalesOffer.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        contracts = {
            offer_id: (label, language)
            for offer_id, label, language in db.execute(
                select(SalesContract.offer_id, SalesContract.customer_label, SalesContract.customer_language).where(
                    SalesContract.offer_id.in_([offer.id for offer in batch]),
                    SalesContract.status != ContractStatus.CANCELLED,
                )
            ).tuples()
        }
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for offer in batch:
            mirrored = contracts.get(offer.id)
            if mirrored is None:
                continue
            label, language = mirrored
            if offer.customer_label == label and offer.customer_language == language:
                stamp_only.append(offer.id)
                continue
            offer.customer_label = label
            offer.customer_language = language
            offer.customer_denorm_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(SalesOffer)
                .where(SalesOffer.id.in_(stamp_only))
                .values(customer_denorm_refreshed_at=now, updated_at=SalesOffer.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def _refresh_table(db: Session, model: type[_Labelled]) -> int:
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model, _follows(model).label("follows"))
            .where(model.customer_id.is_not(None))
            .order_by(model.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(model.id > last_id)
        fetched = db.execute(stmt).all()
        if not fetched:
            return changed
        batch = [row for row, _ in fetched]
        follows = {row.id: bool(flag) for row, flag in fetched}
        last_id = batch[-1].id

        labels = get_customer_sales_labels(db, {row.customer_id for row in batch if row.customer_id is not None})
        entity_labels = _live_entity_labels(db, [row for row in batch if follows[row.id]]) if model is SalesDeal else {}
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.customer_id) if row.customer_id is not None else None
            if current is None or current.merged:
                continue
            if not follows[row.id]:
                # Signed work keeps the name it was signed under; it is only
                # restamped («checked, kept on purpose»), so a row that comes
                # back into open work — the offer a cancelled contract frees,
                # a copy of a signed deal's offer — never brings a stale stamp.
                stamp_only.append(row.id)
                continue
            if isinstance(row, SalesDeal) and entity_labels.get(row.id) != current.label:
                # The deal row shows its live entity (deal_projection.py). An
                # offer or contract a writer held when the job passed kept its
                # old label; its row waits with it for the next night, so the
                # night's reconciliation never finds the two apart.
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


def _live_entity_labels(db: Session, deals: list[Any]) -> dict[uuid.UUID, str | None]:
    """Each deal row's live entity's `customer_label` as committed now: the
    contract's for a contract row, else the offer's. A plain read; the job
    has already committed the offer and contract passes."""

    offer_ids = {d.offer_id for d in deals if d.entity_type == "offer" and d.offer_id is not None}
    contract_ids = {d.contract_id for d in deals if d.entity_type == "contract" and d.contract_id is not None}
    offers = dict(db.execute(select(SalesOffer.id, SalesOffer.customer_label).where(SalesOffer.id.in_(offer_ids))).tuples().all()) if offer_ids else {}
    contracts = (
        dict(db.execute(select(SalesContract.id, SalesContract.customer_label).where(SalesContract.id.in_(contract_ids))).tuples().all())
        if contract_ids
        else {}
    )
    return {
        d.id: (contracts.get(d.contract_id) if d.entity_type == "contract" else offers.get(d.offer_id)) for d in deals
    }


def oldest_customer_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest customer label across the three tables — what the
    worker heartbeat records as `dms.label.age_seconds{label="sales.customer"}`
    (CLAUDE.md rule 10). The nightly job restamps every row whose customer it
    can resolve and whose row it can lock — signed work too, whose label it
    keeps on purpose — so a value well past a day means the job has stopped, a
    customer id names no customer or a merged one whose rows the merge
    consumer has not moved, or a row was held by a writer when the job passed
    (that one clears itself the next night). None when no row names a
    customer."""

    stamps = [_oldest_stamp(db, SalesOffer), _oldest_stamp(db, SalesContract), _oldest_stamp(db, SalesDeal)]
    present = [stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=dt.UTC) for stamp in stamps if stamp]
    if not present:
        return None
    return (utcnow() - min(present)).total_seconds()


def _oldest_stamp(db: Session, model: type[_Labelled]) -> dt.datetime | None:
    return db.scalar(
        select(func.min(model.customer_denorm_refreshed_at)).where(
            model.customer_id.is_not(None), model.customer_denorm_refreshed_at.is_not(None)
        )
    )
