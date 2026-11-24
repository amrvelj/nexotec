"""KAN-590 — the nightly refresh of the contact's label on Sales' open work.

`contact_label` (and, on offers and contracts, `contact_language`) beside
`contact_id` on `sales_offer`, `sales_contract` and `sales_deal` is rule 2's
copy of the contact, written when the buyer is attached
(`offer.apply_contact_change`, the contract's own attach) and when a merge
moves the row (`contact_merge.py`, KAN-580). A contact renamed later kept
the old name on the Sales list, the offer and contract headers and the next
generated document's addressee. This job re-reads the label through
`app.contact.public.get_contact_sales_labels` (no join into the contact
context's tables) and commits per batch in this context alone (ADR-047) —
the rules of `sales.owner_labels` (KAN-404, `owner_label.py`).

**Which rows follow** (Anto, 2026-11-10, ruling 1A): open work only — a
draft or open offer with no live contract of its own buyer, a pending
contract, and the deal rows showing one of them. A confirmed, invoiced or cancelled
contract keeps the name the buyer signed under, and so does its deal row; a
cancelled offer keeps its name too. Rows that do not follow are only
restamped («checked, kept on purpose»), as `sales.owner_labels` restamps
every row it can resolve, so a row that comes back into open work (the offer
a cancelled contract frees, a copied offer) never brings a stale stamp.

**An offer with a live contract of the same buyer mirrors that contract**
(KAN-408: the two never disagree): after the contract pass, such an offer
takes its contract's committed label and language — the new name while the contract is pending,
the signed name once it is signed. Never the contact's directly: a contract
a writer held when the job passed, or one signed between two passes, would
otherwise leave the two apart for good (a held offer is apart for one night
at most). A contract whose buyer was replaced or cleared (ADR-083 amended)
is never mirrored: the offer keeps following its own contact (rule 2). Deal
rows follow their live entity the same way. Documents already generated are frozen and never touched.
`contact_locality` is left as it is (ruling 2, revised): no path fills it.

The correspondence language follows with the label (the plan Anto approved
on 2026-11-10 re-reads "label, language and locality"; locality was then
dropped). For a merged contact's open work this means: the merge consumer
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
from app.contact.public import get_contact_sales_labels
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer

_BATCH = 500
_Labelled = TypeVar("_Labelled", SalesOffer, SalesContract, SalesDeal)

_OPEN_OFFER = (OfferStatus.DRAFT, OfferStatus.OPEN)
_OPEN_CONTRACT = (ContractStatus.PENDING,)


def _follows(model: type[_Labelled]) -> Any:
    """The rows whose contact label follows the contact directly: open work
    without a live contract of the same buyer above it.

    An offer follows while it is draft or open and no live (not cancelled)
    contract naming its own contact was made from it. An offer keeps its
    status when a contract is made from it; such an offer mirrors that
    contract instead (`_mirror_offers_to_contracts`). An offer whose contract
    names another buyer, or none, follows its own contact (rule 2). A
    cancelled contract frees its offer, which follows again."""

    if model is SalesOffer:
        return SalesOffer.status.in_(_OPEN_OFFER) & ~_live_contract_of_the_same_buyer()
    if model is SalesContract:
        return SalesContract.status.in_(_OPEN_CONTRACT)
    # A deal row shows its contract once one exists (`entity_type` flips),
    # with that entity's status as a string (deal_projection.py).
    return or_(
        and_(SalesDeal.entity_type == "offer", SalesDeal.status.in_([s.value for s in _OPEN_OFFER])),
        and_(SalesDeal.entity_type == "contract", SalesDeal.status.in_([s.value for s in _OPEN_CONTRACT])),
    )


def _live_contract_of_the_same_buyer() -> Any:
    """A live (not cancelled) contract made from the offer that names the
    offer's own contact. A pending contract's buyer can be replaced or
    cleared (ADR-083 amended) while the offer keeps its own: then the offer
    follows its own contact, never the contract's (rule 2: a label is the
    copy of the id beside it)."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.offer_id == SalesOffer.id,
            SalesContract.status != ContractStatus.CANCELLED,
            SalesContract.contact_id == SalesOffer.contact_id,
        )
        .correlate(SalesOffer)
        .exists()
    )


def refresh_contact_labels(db: Session) -> int:
    """Re-read the contact's label (and language) on open work — an offer
    without a live contract of its own buyer, a pending contract, then the
    offers mirroring their contract, then the deal rows following their live
    entity; returns how many rows changed. Signed work (and a cancelled offer)
    keeps its label and is only restamped, as `sales.owner_labels` restamps
    every row it can resolve. Walks each table in id order, one commit per
    batch.

    A changed label is written through the ORM (updated_at moves: what the API
    returns changed); `version` is not bumped — the label is a copy no client
    writes, and a bump would make a seller's open workspace refuse its next
    save as stale. A current label only gets `contact_denorm_refreshed_at`
    stamped, with updated_at held. A contact id that names no contact, or a
    merged contact (Sales' merge consumer moves those rows, KAN-185), is left
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
    """Give each draft or open offer with a live contract of the same buyer
    that contract's committed `contact_label` and `contact_language`;
    returns how many offers changed. An offer whose contract names another
    buyer, or none, is not mirrored (it follows its own contact in the offer
    pass); an offer without a buyer is never labelled; an offer whose contact
    id names no contact, or a merged one, is left untouched, stamp included
    (KAN-646). Same batch, lock and stamp rules as `_refresh_table`."""

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(SalesOffer)
            .where(
                SalesOffer.contact_id.is_not(None),
                SalesOffer.status.in_(_OPEN_OFFER),
                _live_contract_of_the_same_buyer(),
            )
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
            (offer_id, contact_id): (label, language)
            for offer_id, contact_id, label, language in db.execute(
                select(
                    SalesContract.offer_id,
                    SalesContract.contact_id,
                    SalesContract.contact_label,
                    SalesContract.contact_language,
                ).where(
                    SalesContract.offer_id.in_([offer.id for offer in batch]),
                    SalesContract.status != ContractStatus.CANCELLED,
                )
            ).tuples()
        }
        labels = get_contact_sales_labels(db, {offer.contact_id for offer in batch if offer.contact_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for offer in batch:
            # As in `_refresh_table` (KAN-646): a contact id that names no
            # contact, or a merged contact, leaves the offer untouched —
            # its contract and deal row were left too, and the stale stamp
            # is what the sync-age alarm shows.
            current = labels.get(offer.contact_id) if offer.contact_id is not None else None
            if current is None or current.merged:
                continue
            # Re-checked on the committed contract: its buyer may have changed
            # since the batch was read; then the offer waits for the next night.
            mirrored = contracts.get((offer.id, offer.contact_id))
            if mirrored is None:
                continue
            label, language = mirrored
            if offer.contact_label == label and offer.contact_language == language:
                stamp_only.append(offer.id)
                continue
            offer.contact_label = label
            offer.contact_language = language
            offer.contact_denorm_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(SalesOffer)
                .where(SalesOffer.id.in_(stamp_only))
                .values(contact_denorm_refreshed_at=now, updated_at=SalesOffer.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def _refresh_table(db: Session, model: type[_Labelled]) -> int:
    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(model, _follows(model).label("follows"))
            .where(model.contact_id.is_not(None))
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

        labels = get_contact_sales_labels(db, {row.contact_id for row in batch if row.contact_id is not None})
        entity_labels = _live_entity_labels(db, [row for row in batch if follows[row.id]]) if model is SalesDeal else {}
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.contact_id) if row.contact_id is not None else None
            if current is None or current.merged:
                continue
            if not follows[row.id]:
                # Signed work keeps the name it was signed under (and an offer
                # with a live contract of its buyer waits for the mirror); it is only
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
            same_language = isinstance(row, SalesDeal) or row.contact_language == current.language
            if current.label == row.contact_label and same_language:
                stamp_only.append(row.id)
                continue
            row.contact_label = current.label
            if not isinstance(row, SalesDeal):
                row.contact_language = current.language
            row.contact_denorm_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(model)
                .where(model.id.in_(stamp_only))
                .values(contact_denorm_refreshed_at=now, updated_at=model.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def _live_entity_labels(db: Session, deals: list[Any]) -> dict[uuid.UUID, str | None]:
    """Each deal row's live entity's `contact_label` as committed now: the
    contract's for a contract row, else the offer's. A plain read; the job
    has already committed the offer and contract passes."""

    offer_ids = {d.offer_id for d in deals if d.entity_type == "offer" and d.offer_id is not None}
    contract_ids = {d.contract_id for d in deals if d.entity_type == "contract" and d.contract_id is not None}
    offers = dict(db.execute(select(SalesOffer.id, SalesOffer.contact_label).where(SalesOffer.id.in_(offer_ids))).tuples().all()) if offer_ids else {}
    contracts = (
        dict(db.execute(select(SalesContract.id, SalesContract.contact_label).where(SalesContract.id.in_(contract_ids))).tuples().all())
        if contract_ids
        else {}
    )
    return {
        d.id: (contracts.get(d.contract_id) if d.entity_type == "contract" else offers.get(d.offer_id)) for d in deals
    }


def oldest_contact_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest contact label across the three tables — what the
    worker heartbeat records as `dms.label.age_seconds{label="sales.contact"}`
    (CLAUDE.md rule 10). The nightly job restamps every row whose contact it
    can resolve and whose row it can lock — signed work too, whose label it
    keeps on purpose — so a value well past a day means the job has stopped, a
    contact id names no contact or a merged one whose rows the merge
    consumer has not moved, or a row was held by a writer when the job passed
    (that one clears itself the next night). None when no row names a
    contact."""

    stamps = [_oldest_stamp(db, SalesOffer), _oldest_stamp(db, SalesContract), _oldest_stamp(db, SalesDeal)]
    present = [stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=dt.UTC) for stamp in stamps if stamp]
    if not present:
        return None
    return (utcnow() - min(present)).total_seconds()


def _oldest_stamp(db: Session, model: type[_Labelled]) -> dt.datetime | None:
    return db.scalar(
        select(func.min(model.contact_denorm_refreshed_at)).where(
            model.contact_id.is_not(None), model.contact_denorm_refreshed_at.is_not(None)
        )
    )
