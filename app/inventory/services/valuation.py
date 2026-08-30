"""WP-7 PR-9 (ADR-066/ADR-048) — Stock reads the denormalized pointer.
`set_valuation_ref` (WP-8 PR-5) is the write half, added now that
app.valuation exists — Pattern B (ADR-047, own commit), called BY the
valuation module or by Sales at contract confirmation (PR-6, once a
trade-in becomes a real pipeline stock item), never written ambiently
from inside inventory itself.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.idempotency import find_cached_response, store_response
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
    item.valuation_ref_id = valuation_id
    item.valuation_ref_amount = amount
    item.valuation_ref_valued_at = valued_at
    item.valuation_ref_source = source
    db.flush()

    result = ValuationRefRead(valuation_id=valuation_id, amount=amount, valued_at=valued_at, source=source)
    store_response(
        db, tenant_id=tenant_id, key=idempotency_key, path=path, body=None,
        response_status=200, response_body=result.model_dump(mode="json"),
    )
    db.commit()
    return result
