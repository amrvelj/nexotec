"""WP-7 PR-9: factory options (FR-I-22) + the valuation reader stub
(ADR-066/ADR-048)."""

import uuid
from decimal import Decimal

import pytest

from app.core.errors import ConflictError
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.pricing import OptionInput
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pricing import list_options, marketplace_equipment_codes, set_options
from app.inventory.services.stock_item import create_stock_item
from app.inventory.services.valuation import get_valuation_ref


def _make_item(db_session, tenant_id, condition):
    return create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=condition),
        actor_id=uuid.uuid4(),
    )


def test_set_options_computes_list_price_from_base_plus_options(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id, StockItemCondition.NEW)

    updated = set_options(
        db_session, item=item, base_price=Decimal("30000.00"),
        options=[
            OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"), equipment_code="led_scheinwerfer"),
            OptionInput(code="NAV", label="Navigationssystem", price=Decimal("1200.00")),
        ],
        actor_id=uuid.uuid4(),
    )
    assert updated.base_price == Decimal("30000.00")
    assert updated.list_price == Decimal("32000.00")


def test_options_refused_on_a_used_car(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id, StockItemCondition.USED)

    with pytest.raises(ConflictError):
        set_options(
            db_session, item=item, base_price=Decimal("20000.00"),
            options=[OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"))],
            actor_id=uuid.uuid4(),
        )


def test_empty_options_on_a_used_car_is_allowed(db_session):
    """Setting basePrice with zero options is just recording a base price
    — the refusal is specifically about ITEMISING, not about touching
    base_price at all."""

    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id, StockItemCondition.USED)
    updated = set_options(db_session, item=item, base_price=Decimal("20000.00"), options=[], actor_id=uuid.uuid4())
    assert updated.base_price == Decimal("20000.00")
    assert updated.list_price == Decimal("20000.00")


def test_marketplace_equipment_codes_is_the_same_source_as_pricing(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id, StockItemCondition.NEW)
    set_options(
        db_session, item=item, base_price=Decimal("30000.00"),
        options=[
            OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"), equipment_code="led_scheinwerfer"),
            OptionInput(code="NAV", label="Navigationssystem", price=Decimal("1200.00")),  # no equipment_code
        ],
        actor_id=uuid.uuid4(),
    )
    options = list_options(db_session, tenant_id=tenant_id, stock_item_id=item.id)
    codes = marketplace_equipment_codes(options)
    assert codes == ["led_scheinwerfer"]


def test_valuation_ref_defaults_to_all_none(db_session):
    tenant_id = uuid.uuid4()
    item = _make_item(db_session, tenant_id, StockItemCondition.USED)
    ref = get_valuation_ref(db_session, tenant_id=tenant_id, stock_item_id=item.id)
    assert ref.valuation_id is None
    assert ref.amount is None


def test_no_valuation_write_endpoint_exists(client):
    """ADR-066/ADR-048 — Stock is a reader only; the real create/update
    path belongs to the valuation module (WP-8), which doesn't exist yet."""

    schema = client.app.openapi()
    valuation_path = schema["paths"].get("/v1/inventory/stock-items/{stock_item_id}/valuation", {})
    assert set(valuation_path.keys()) == {"get"}
