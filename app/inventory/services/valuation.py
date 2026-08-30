"""WP-7 PR-9 (ADR-066/ADR-048) — Stock reads the denormalized pointer
only. No write function exists here on purpose: the valuation module
(WP-8) is the single writer, and until it ships there is no cross-context
call to make either — see app.inventory.models.stock_item's own
valuation_ref_* columns for the reasoning.
"""

import uuid

from sqlalchemy.orm import Session

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
