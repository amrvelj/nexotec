"""WP-7 PR-9 (ADR-066/ADR-048) — Stock reads the denormalized pointer.

It is written in two ways, both through `apply_valuation_ref`:
- KAN-101: `pipeline.handle_sales_contract_confirmed` sets it in the same
  transaction that creates a trade-in's pipeline stock item — the item
  does not exist when Sales confirms the contract, so no Sales call could
  target it;
- `set_valuation_ref` (WP-8 PR-5) — Pattern B (ADR-047, own commit), for a
  caller in another context. No production code calls it yet.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.idempotency import find_cached_response, store_response
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.valuation import ValuationRefRead
from app.inventory.services.stock_item import get_stock_item_or_404


def get_valuation_ref(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> ValuationRefRead:
    item = get_stock_item_or_404(db, tenant_id, stock_item_id)
    return ValuationRefRead(
        valuation_id=item.valuation_ref_id,
        amount=item.valuation_ref_amount,
        valued_at=item.valuation_ref_valued_at,
        source=item.valuation_ref_source,
    )


def apply_valuation_ref(
    item: StockItem, *, valuation_id: uuid.UUID, amount: Decimal, valued_at: dt.datetime, source: str
) -> None:
    """Sets the four pointer columns; the caller flushes and commits."""

    item.valuation_ref_id = valuation_id
    item.valuation_ref_amount = amount
    item.valuation_ref_valued_at = valued_at
    item.valuation_ref_source = source


def set_valuation_ref(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    valuation_id: uuid.UUID,
    amount: Decimal,
    valued_at: dt.datetime,
    source: str,
    idempotency_key: str,
) -> ValuationRefRead:
    """Pattern B (own commit) — same idiom as reservation.py's
    reserve/release, since this too is a cross-context write the caller
    must invoke from OUTSIDE its own transaction.
    """

    path = f"inventory.set_valuation_ref:{stock_item_id}"
    cached = find_cached_response(db, tenant_id=tenant_id, key=idempotency_key, path=path, body=None)
    if cached is not None:
        return ValuationRefRead.model_validate(cached.response_body)

    item = get_stock_item_or_404(db, tenant_id, stock_item_id)
    apply_valuation_ref(item, valuation_id=valuation_id, amount=amount, valued_at=valued_at, source=source)
    db.flush()

    result = ValuationRefRead(valuation_id=valuation_id, amount=amount, valued_at=valued_at, source=source)
    store_response(
        db, tenant_id=tenant_id, key=idempotency_key, path=path, body=None,
        response_status=200, response_body=result.model_dump(mode="json"),
    )
    db.commit()
    return result
