"""WP-8 PR-5: app.inventory.public.set_valuation_ref — the write half of
the ADR-066/ADR-048 pointer, added now that app.valuation exists. Pattern
B (ADR-047): own commit, idempotency-key required.
"""

import uuid
from decimal import Decimal

from app.core.base import utcnow
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.public import get_stock_item_or_404, set_valuation_ref
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item


def test_set_valuation_ref_writes_the_pointer(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Skoda Octavia Combi 1.5 TSI", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    valuation_id = uuid.uuid4()
    valued_at = utcnow()

    result = set_valuation_ref(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, valuation_id=valuation_id,
        amount=Decimal("17950.00"), valued_at=valued_at, source="auto_i_dat",
        idempotency_key=f"test:{uuid.uuid4()}",
    )
    assert result.valuation_id == valuation_id
    assert result.amount == Decimal("17950.00")

    refetched = get_stock_item_or_404(db_session, tenant_id, item.id)
    assert refetched.valuation_ref_id == valuation_id
    assert refetched.valuation_ref_amount == Decimal("17950.00")
    assert refetched.valuation_ref_source == "auto_i_dat"


def test_set_valuation_ref_is_idempotent(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Fiat 500", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    key = f"test:{uuid.uuid4()}"
    valuation_id = uuid.uuid4()

    first = set_valuation_ref(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, valuation_id=valuation_id,
        amount=Decimal("11950.00"), valued_at=utcnow(), source="manual", idempotency_key=key,
    )
    second = set_valuation_ref(
        db_session, tenant_id=tenant_id, stock_item_id=item.id, valuation_id=valuation_id,
        amount=Decimal("11950.00"), valued_at=utcnow(), source="manual", idempotency_key=key,
    )
    assert first.amount == second.amount
