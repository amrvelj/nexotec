"""The responsible seller on offers and contracts, and Sales' ownership
provider for the deactivation step (KAN-200; FR-A-37, D-A-35).

`owner_user_id` is who the open deal belongs to — the creator by default,
a colleague after a hand-over, nobody once a leaver's deals went to the
unassigned queue. `created_by` stays what it was: who typed it (A-RULE-5).
The label beside the id is rule 2's denormalised copy, read from the user
row at the moment of writing.

`SalesOwnershipProvider` is what platform's deactivation asks through
`app.core.ownership` — never by importing this module. Its `transfer` runs
on a session of its own and commits there (ADR-047): it is a Sales write,
inside Sales' transaction, that platform's status change neither contains
nor undoes.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.core.ownership import OwnedGroup, OwnershipRefusal, OwnershipTransfer
from app.platform.public import get_user_acting_in, list_user_dealership_ids
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection

logger = logging.getLogger(__name__)

MODULE = "sales"
GROUP_OFFERS = "offers"
GROUP_CONTRACTS = "contracts"
_OPEN_OFFER_STATUSES = (OfferStatus.DRAFT, OfferStatus.OPEN)
_OPEN_CONTRACT_STATUSES = (ContractStatus.PENDING,)


def offer_is_open() -> ColumnElement[bool]:
    """An offer still being worked on: draft or open AND no live contract made
    from it. An offer keeps its status when a contract is created from it
    (`OfferStatus` has no "contracted"), so the status alone counts a signed
    deal's offer as open — the contract is the live record then, and its own
    group carries the hand-over. One definition for the ownership count, the
    transfer and the nightly reconciliation."""

    live_contract = (
        select(SalesContract.id)
        .where(SalesContract.offer_id == SalesOffer.id, SalesContract.status != ContractStatus.CANCELLED)
        .correlate(SalesOffer)
        .exists()
    )
    return SalesOffer.status.in_(_OPEN_OFFER_STATUSES) & ~live_contract


_NO_OWNER: dict[str, Any] = {"owner_user_id": None, "owner_label": None, "owner_label_refreshed_at": None}
_SELLER_STATUSES = ("active", "invited")


def _seller_in(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> Any | None:
    """The user when they act in this dealership — homed there or an active
    member (a sister-Betrieb member sells there too) — and still hold or
    await access: a suspended or deactivated user cannot be responsible for
    an open deal. None otherwise, a platform staff member creating an offer
    in a Betrieb they are not a member of included."""

    if user_id is None:
        return None
    user = get_user_acting_in(db, dealership_id=tenant_id, user_id=user_id)
    if user is None or user.status.value not in _SELLER_STATUSES:
        return None
    return user


def _label(user: Any) -> str:
    return f"{user.first_name} {user.last_name}".strip()


def default_owner(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """The columns a new offer or contract starts with: the creator as the
    responsible seller when they act in the dealership."""

    user = _seller_in(db, tenant_id=tenant_id, user_id=actor_id)
    if user is None:
        return dict(_NO_OWNER)
    return {"owner_user_id": actor_id, "owner_label": _label(user), "owner_label_refreshed_at": utcnow()}


def resolve_owner_change(db: Session, *, tenant_id: uuid.UUID, owner_user_id: uuid.UUID | None) -> dict[str, Any]:
    """A seller set through the workspace: must act in this dealership and
    hold or await access (422 otherwise); null clears the seller."""

    if owner_user_id is None:
        return dict(_NO_OWNER)
    user = _seller_in(db, tenant_id=tenant_id, user_id=owner_user_id)
    if user is None:
        raise UnprocessableEntityError(
            "ownerUserId is not an active user of your dealership.", details={"ownerUserId": str(owner_user_id)}
        )
    return {"owner_user_id": owner_user_id, "owner_label": _label(user), "owner_label_refreshed_at": utcnow()}


def apply_owner(deal: SalesOffer | SalesContract, owner: dict[str, Any]) -> None:
    deal.owner_user_id = owner["owner_user_id"]
    deal.owner_label = owner["owner_label"]
    deal.owner_label_refreshed_at = owner["owner_label_refreshed_at"]


class SalesOwnershipProvider:
    module = MODULE

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        # Everywhere the user owns them, not only their home: a member of a
        # sister Betrieb sells there and those deals are theirs too.
        offers = db.scalar(select(func.count(SalesOffer.id)).where(SalesOffer.owner_user_id == user_id, offer_is_open()))
        contracts = db.scalar(
            select(func.count(SalesContract.id)).where(
                SalesContract.owner_user_id == user_id, SalesContract.status.in_(_OPEN_CONTRACT_STATUSES)
            )
        )
        groups: list[OwnedGroup] = []
        if offers:
            groups.append(OwnedGroup(module=MODULE, group=GROUP_OFFERS, count=int(offers)))
        if contracts:
            groups.append(OwnedGroup(module=MODULE, group=GROUP_CONTRACTS, count=int(contracts)))
        return groups

    def refuses(self, db: Session, *, from_user_id: uuid.UUID, transfer: OwnershipTransfer) -> OwnershipRefusal | None:
        if transfer.to_user_id is None or transfer.group not in (GROUP_OFFERS, GROUP_CONTRACTS):
            return None
        rows = _open_rows(db, from_user_id=from_user_id, group=transfer.group, lock=False)
        return _colleague_refusal(db, to_user_id=transfer.to_user_id, rows=rows, group=transfer.group)

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
        reason: str | None,
    ) -> int:
        if transfer.group not in (GROUP_OFFERS, GROUP_CONTRACTS):
            raise ValueError(f"sales owns no group '{transfer.group}'")
        db = session_factory()
        try:
            moved = _transfer_group(
                db,
                from_user_id=from_user_id,
                group=transfer.group,
                to_user_id=transfer.to_user_id,
                to_label=transfer.to_label,
                actor_id=actor_id,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        logger.info(
            "sales.ownership.transferred group=%s from=%s to=%s count=%d",
            transfer.group,
            from_user_id,
            transfer.to_user_id,
            moved,
            extra={"group": transfer.group, "fromUserId": str(from_user_id), "count": moved},
        )
        return moved


def _open_rows(db: Session, *, from_user_id: uuid.UUID, group: str, lock: bool) -> list[Any]:
    """The user's open offers or pending contracts in every Betrieb, in id
    order; `lock` takes the row locks a transfer needs (KAN-147's order:
    offer → contract → deal)."""

    stmt: Any
    if group == GROUP_OFFERS:
        stmt = (
            select(SalesOffer)
            .where(SalesOffer.owner_user_id == from_user_id, offer_is_open())
            .order_by(SalesOffer.id)
        )
    else:
        stmt = (
            select(SalesContract)
            .where(SalesContract.owner_user_id == from_user_id, SalesContract.status.in_(_OPEN_CONTRACT_STATUSES))
            .order_by(SalesContract.id)
        )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt).all())


def _assert_colleague_acts_everywhere(
    db: Session, *, to_user_id: uuid.UUID | None, rows: list[Any], group: str
) -> None:
    """A hand-over to a colleague moves records of every Betrieb the leaver
    owned them in; the colleague must be able to act in each (home or
    active membership), or a sister Betrieb's deals would be owned by
    someone who cannot open them. Refused as a whole, before any write."""

    refusal = _colleague_refusal(db, to_user_id=to_user_id, rows=rows, group=group)
    if refusal is not None:
        raise UnprocessableEntityError(
            refusal.message,
            details={
                "module": MODULE,
                "group": group,
                "toUserId": str(to_user_id),
                "reason": refusal.code,
                "count": refusal.count,
            },
        )


COLLEAGUE_CANNOT_ACT_THERE = "colleague_cannot_act_there"


def _colleague_refusal(
    db: Session, *, to_user_id: uuid.UUID | None, rows: list[Any], group: str
) -> OwnershipRefusal | None:
    """Never the other Betrieb's id (a 404-not-403 world: an id is a fact the
    caller may not hold); the count says how much is elsewhere."""

    if to_user_id is None:
        return None
    allowed = list_user_dealership_ids(db, user_id=to_user_id)
    elsewhere = sum(1 for row in rows if row.tenant_id not in allowed)
    if not elsewhere:
        return None
    return OwnershipRefusal(
        code=COLLEAGUE_CANNOT_ACT_THERE,
        message=(
            f"sales/{group}: {elsewhere} of these records belong to another business the colleague cannot act in — "
            "leave the group unassigned (null), and that business hands them over from its own queue, or choose a "
            "colleague who is a member there."
        ),
        count=elsewhere,
    )


def _transfer_group(
    db: Session,
    *,
    from_user_id: uuid.UUID,
    group: str,
    to_user_id: uuid.UUID | None,
    to_label: str | None,
    actor_id: uuid.UUID | None,
) -> int:
    """Row locks offer → contract → deal, the order every Sales writer takes
    (KAN-147); `upsert_deal_projection` flushes the entity before the deal."""

    owner = {
        "owner_user_id": to_user_id,
        "owner_label": to_label if to_user_id is not None else None,
        "owner_label_refreshed_at": utcnow() if to_user_id is not None else None,
    }
    moved = 0
    if group == GROUP_OFFERS:
        rows = _open_rows(db, from_user_id=from_user_id, group=group, lock=True)
        _assert_colleague_acts_everywhere(db, to_user_id=to_user_id, rows=rows, group=group)
        for offer in rows:
            apply_owner(offer, owner)
            offer.updated_by = actor_id
            offer.version += 1
            moved += 1
            # The deal row follows its live entity: an offer with a live
            # contract is not open (`offer_is_open`), so it is not in `rows`
            # and never re-projects a contract's deal row from the offer.
            upsert_deal_projection(db, offer=offer)
        return moved
    rows_c = _open_rows(db, from_user_id=from_user_id, group=group, lock=True)
    _assert_colleague_acts_everywhere(db, to_user_id=to_user_id, rows=rows_c, group=group)
    for contract in rows_c:
        apply_owner(contract, owner)
        contract.updated_by = actor_id
        contract.version += 1
        moved += 1
        upsert_deal_projection(db, contract=contract)
    return moved


def deal_owner_columns(deal: SalesDeal, source: SalesOffer | SalesContract) -> None:
    deal.owner_user_id = source.owner_user_id
    deal.owner_label = source.owner_label
    deal.owner_label_refreshed_at = source.owner_label_refreshed_at
