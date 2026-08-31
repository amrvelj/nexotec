"""Customer-merge repointing for sales_offer/sales_contract (WP-8 PR-7).

A merge must repoint the NEW tables too, not just the retired
`transaction` table repoint_customer_transactions already handles.
Pattern B (ADR-047, own commit) — mirrors
app.customer.services.customer.repoint_vehicle_party exactly, called
AFTER the customer side has already committed its own repointing, never
joining that transaction. A failure here is repaired by nightly
reconciliation, not by rolling back the merge — the correct trade per
ADR-047, not an oversight.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer


def repoint_customer_sales_records(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> int:
    offers = list(db.scalars(select(SalesOffer).where(SalesOffer.customer_id == duplicate_id)).all())
    for offer in offers:
        offer.customer_id = target_id

    contracts = list(db.scalars(select(SalesContract).where(SalesContract.customer_id == duplicate_id)).all())
    for contract in contracts:
        contract.customer_id = target_id

    deals = list(db.scalars(select(SalesDeal).where(SalesDeal.customer_id == duplicate_id)).all())
    for deal in deals:
        deal.customer_id = target_id

    db.commit()
    return len(offers) + len(contracts)
