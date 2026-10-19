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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.ownership import OwnedGroup, OwnershipTransfer
from app.platform.public import get_user_or_404
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


def owner_label_of(db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> str | None:
    """The display label for a seller of this dealership, or None when the
    id names no user of it — a platform staff member creating an offer in a
    Betrieb they are not homed in owns nothing there."""

    if user_id is None:
        return None
    try:
        user = get_user_or_404(db, tenant_id, user_id)
    except NotFoundError:
        return None
    return f"{user.first_name} {user.last_name}".strip()


def default_owner(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """The columns a new offer or contract starts with: the creator as the
    responsible seller when they are a user of the dealership."""

    label = owner_label_of(db, tenant_id=tenant_id, user_id=actor_id)
    if label is None:
        return {"owner_user_id": None, "owner_label": None, "owner_label_refreshed_at": None}
    return {"owner_user_id": actor_id, "owner_label": label, "owner_label_refreshed_at": utcnow()}


def resolve_owner_change(db: Session, *, tenant_id: uuid.UUID, owner_user_id: uuid.UUID | None) -> dict[str, Any]:
    """A seller set through the workspace: must be a user of this dealership
    (422 otherwise); null clears the seller."""

    if owner_user_id is None:
        return {"owner_user_id": None, "owner_label": None, "owner_label_refreshed_at": None}
    label = owner_label_of(db, tenant_id=tenant_id, user_id=owner_user_id)
    if label is None:
        raise UnprocessableEntityError(
            "ownerUserId is not a user of your dealership.", details={"ownerUserId": str(owner_user_id)}
        )
    return {"owner_user_id": owner_user_id, "owner_label": label, "owner_label_refreshed_at": utcnow()}


def apply_owner(deal: SalesOffer | SalesContract, owner: dict[str, Any]) -> None:
    deal.owner_user_id = owner["owner_user_id"]
    deal.owner_label = owner["owner_label"]
    deal.owner_label_refreshed_at = owner["owner_label_refreshed_at"]


class SalesOwnershipProvider:
    module = MODULE

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        offers = db.scalar(
            select(func.count(SalesOffer.id)).where(
                SalesOffer.tenant_id == dealership_id,
                SalesOffer.owner_user_id == user_id,
                SalesOffer.status.in_(_OPEN_OFFER_STATUSES),
            )
        )
        contracts = db.scalar(
            select(func.count(SalesContract.id)).where(
                SalesContract.tenant_id == dealership_id,
                SalesContract.owner_user_id == user_id,
                SalesContract.status.in_(_OPEN_CONTRACT_STATUSES),
            )
        )
        groups: list[OwnedGroup] = []
        if offers:
            groups.append(OwnedGroup(module=MODULE, group=GROUP_OFFERS, count=int(offers)))
        if contracts:
            groups.append(OwnedGroup(module=MODULE, group=GROUP_CONTRACTS, count=int(contracts)))
        return groups

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
    ) -> int:
        if transfer.group not in (GROUP_OFFERS, GROUP_CONTRACTS):
            raise ValueError(f"sales owns no group '{transfer.group}'")
        db = session_factory()
        try:
            moved = _transfer_group(
                db,
                dealership_id=dealership_id,
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


def _transfer_group(
    db: Session,
    *,
    dealership_id: uuid.UUID,
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
        rows = list(
            db.scalars(
                select(SalesOffer)
                .where(
                    SalesOffer.tenant_id == dealership_id,
                    SalesOffer.owner_user_id == from_user_id,
                    SalesOffer.status.in_(_OPEN_OFFER_STATUSES),
                )
                .order_by(SalesOffer.id)
                .with_for_update()
            ).all()
        )
        for offer in rows:
            apply_owner(offer, owner)
            offer.updated_by = actor_id
            offer.version += 1
            moved += 1
            # The deal row follows its live entity: an offer that already has
            # a contract is not open, so it is not in `rows`.
            upsert_deal_projection(db, offer=offer)
        return moved
    rows_c = list(
        db.scalars(
            select(SalesContract)
            .where(
                SalesContract.tenant_id == dealership_id,
                SalesContract.owner_user_id == from_user_id,
                SalesContract.status.in_(_OPEN_CONTRACT_STATUSES),
            )
            .order_by(SalesContract.id)
            .with_for_update()
        ).all()
    )
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
