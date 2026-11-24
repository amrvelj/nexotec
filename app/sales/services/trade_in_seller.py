"""KAN-189 (Anto, 2026-11-02, option A): who traded a bought car in.

When Stock books the purchase of a trade-in's pipeline item
(`inventory.stock_item.purchased` with `originRole: trade_in`), the contact
context records the contract's contact as the car's former owner and
keeper. The contract and its contact are Sales' facts. Since KAN-632 they
travel in the events (`sales.contract.confirmed`'s `contactId`/`signedAt`,
which Stock carries on as `tradeInContactId`/`tradeInSignedAt`); this read,
through `app.sales.public`, serves only
`scripts/backfill_trade_in_seller_on_stock_items.py` — the worker's fallback
that asked it for an event without them is retired (KAN-674). Contact sits
below sales and never imports it (ADR-131). A read, nothing else (CLAUDE.md rule 2).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract


@dataclasses.dataclass(frozen=True)
class TradeInSeller:
    """The contact who traded the car in on `contract_id`, and since when the
    system knows they held it: the contract's signature, else its creation."""

    contract_id: uuid.UUID
    contact_id: uuid.UUID
    known_since: dt.datetime


def get_trade_in_seller(db: Session, *, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> TradeInSeller | None:
    """None when this Betrieb has no such contract, or it names no contact
    (a contract is never confirmed without one, KAN-307, so this is a
    contract written before that rule)."""

    row = db.execute(
        select(SalesContract.contact_id, SalesContract.signed_at, SalesContract.created_at).where(
            SalesContract.tenant_id == tenant_id, SalesContract.id == contract_id
        )
    ).one_or_none()
    if row is None or row.contact_id is None:
        return None
    return TradeInSeller(
        contract_id=contract_id, contact_id=row.contact_id, known_since=row.signed_at or row.created_at
    )
