"""KAN-189 (Anto, 2026-11-02, option A): who traded a bought car in.

When Stock books the purchase of a trade-in's pipeline item
(`inventory.stock_item.purchased` with `originRole: trade_in`), the customer
context records the contract's customer as the car's former owner and
keeper. The contract and its customer are Sales' facts, so the customer
context asks for them here, through `app.sales.public` — a read, nothing
else (CLAUDE.md rule 2; classified in
tests/architecture/test_adr_047_no_shared_transaction.py).
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract


@dataclasses.dataclass(frozen=True)
class TradeInSeller:
    """The customer who traded the car in on `contract_id`, and since when the
    system knows they held it: the contract's signature, else its creation."""

    contract_id: uuid.UUID
    customer_id: uuid.UUID
    known_since: dt.datetime


def get_trade_in_seller(db: Session, *, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> TradeInSeller | None:
    """None when this Betrieb has no such contract, or it names no customer
    (a contract is never confirmed without one, KAN-307, so this is a
    contract written before that rule)."""

    row = db.execute(
        select(SalesContract.customer_id, SalesContract.signed_at, SalesContract.created_at).where(
            SalesContract.tenant_id == tenant_id, SalesContract.id == contract_id
        )
    ).one_or_none()
    if row is None or row.customer_id is None:
        return None
    return TradeInSeller(
        contract_id=contract_id, customer_id=row.customer_id, known_since=row.signed_at or row.created_at
    )
