"""Contract facts as plain data, for Stock's projection backfill (KAN-630).

Stock sits below Sales (ADR-131) and keeps its own projection of the
contracts its orphan sweep and «Einkauf stornieren» need, fed by Sales'
contract events. A contract older than Stock's consumers was never fed;
`scripts/backfill_inventory_sales_contract_projection.py` reads it here, a
page at a time, and writes Stock's rows through `app.inventory.public` on
a session of its own (ADR-047). A read: no lock, no write, nothing flushed.
Its own module rather than app.sales.services.contract, so
`app.sales.public` does not load the contract service for every importer.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract


@dataclass(frozen=True)
class ContractFacts:
    """What Stock's projection holds of one contract. `status` is the
    stored value (`pending` | `confirmed` | `invoiced` | `cancelled`)."""

    tenant_id: uuid.UUID
    contract_id: uuid.UUID
    contract_number: str
    status: str
    stock_item_id: uuid.UUID | None
    invoice_requested_at: dt.datetime | None


def list_contract_facts(db: Session, *, after: uuid.UUID | None, limit: int) -> list[ContractFacts]:
    """Every contract of every dealership, in id order, `limit` at a time
    after the id `after` (None: from the start)."""

    stmt = select(
        SalesContract.tenant_id,
        SalesContract.id,
        SalesContract.contract_number,
        SalesContract.status,
        SalesContract.stock_item_id,
        SalesContract.invoice_requested_at,
    ).order_by(SalesContract.id)
    if after is not None:
        stmt = stmt.where(SalesContract.id > after)
    return [
        ContractFacts(
            tenant_id=row.tenant_id,
            contract_id=row.id,
            contract_number=row.contract_number,
            status=row.status.value,
            stock_item_id=row.stock_item_id,
            invoice_requested_at=row.invoice_requested_at,
        )
        for row in db.execute(stmt.limit(limit)).all()
    ]
