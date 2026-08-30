"""Offer/contract number allocation (WP-8 PR-1). Row-lock-then-increment,
one counter per (tenant_id, series) — the same idiom as
app.inventory.services.stock_item.allocate_stock_number,
app.vehicle.services.vehicle_mdm.allocate_vehicle_number, and
app.customer.services.customer._allocate_customer_number. Scoped per
TENANT (dealership), like stock numbers, since offers and contracts are
this dealership's own paperwork, not a group or global fact.
"""

import uuid

from sqlalchemy.orm import Session

from app.sales.models.deal import SalesNumberSequence

_OFFER_SERIES = "offer"
_CONTRACT_SERIES = "contract"


def _allocate(db: Session, *, tenant_id: uuid.UUID, series: str, prefix: str) -> str:
    key = (tenant_id, series)
    row = db.get(SalesNumberSequence, key, with_for_update=True)
    if row is None:
        row = SalesNumberSequence(tenant_id=tenant_id, series=series, next_value=1)
        db.add(row)
        db.flush()
        row = db.get(SalesNumberSequence, key, with_for_update=True)
        assert row is not None, "just-flushed SalesNumberSequence row vanished before it could be re-read"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"{prefix}-{value:06d}"


def allocate_offer_number(db: Session, tenant_id: uuid.UUID) -> str:
    return _allocate(db, tenant_id=tenant_id, series=_OFFER_SERIES, prefix="O")


def allocate_contract_number(db: Session, tenant_id: uuid.UUID) -> str:
    return _allocate(db, tenant_id=tenant_id, series=_CONTRACT_SERIES, prefix="C")
