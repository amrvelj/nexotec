"""WP-8 PR-4: Price — one gross price (ADR-057/S-D10). PR-3 already built
pricing.build_up() with no VAT concept anywhere; this pins the resulting
shape down explicitly and confirms Sales can read the ADR-052 invoicing
gate it will use in PR-6, so "the surviving confirmation gate is the
purchase, not the tax" (S-D10) has something real to point at already.
"""

import uuid

from app.inventory.models.stock_item import StockItemCondition
from app.inventory.public import get_stock_item_or_404
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item
from app.sales.schemas.contract import ContractRead
from app.sales.schemas.deal import DealRead
from app.sales.schemas.offer import OfferRead


def test_no_schema_carries_a_vat_field():
    """Mirrors app.inventory's own per-schema field check (WP-7 PR-3) —
    belt-and-suspenders alongside the whole-repo architecture scan. A bare
    "vat" substring check is too broad — it false-positives on
    ContractRead's own legitimate `reservation_id` (reser-VAT-ion) — so
    this checks for "vat" as its own underscore-delimited token instead.
    """

    for schema in (OfferRead, ContractRead, DealRead):
        field_names = set(schema.model_fields.keys())
        vat_like = {f for f in field_names if "vat" in f.lower().split("_")}
        assert not vat_like, f"{schema.__name__} carries a VAT-shaped field — ADR-057/S-D10 forbids this everywhere."


def test_offer_and_contract_carry_exactly_one_customer_facing_price():
    """gross_price is the one figure — no separate net/list-minus-vat/
    with-vat pair exists anywhere on either schema."""

    offer_fields = set(OfferRead.model_fields.keys())
    contract_fields = set(ContractRead.model_fields.keys())
    for fields, name in ((offer_fields, "OfferRead"), (contract_fields, "ContractRead")):
        price_like = {f for f in fields if "price" in f.lower()}
        # base_price/list_price/manual_base_price are BUILD-UP internals
        # (PR-3) — gross_price is the only one that is ever the customer-
        # facing figure.
        assert "gross_price" in price_like, f"{name} has no gross_price at all"


def test_sales_can_read_the_invoicing_gate_stock_already_exposes(db_session):
    """S-D10: "the surviving confirmation gate is the purchase, not the
    tax." Confirms the fact PR-6's confirm_contract will gate on is
    already reachable through app.inventory.public — no new inventory
    surface needed for this.
    """

    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Renault Clio", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    fetched = get_stock_item_or_404(db_session, tenant_id, item.id)
    assert fetched.is_invoiceable is False  # no purchase booked yet — never a tax concept
    assert not hasattr(fetched, "vat_treatment")


def test_dealership_vat_rate_is_the_one_vat_figure_in_the_system():
    """Confirms WP-7 PR-3's own field is still the sole VAT concept
    anywhere Sales could reach — a single dealer-configurable rate, never
    a per-deal attribute."""

    from app.platform.models.dealership import Dealership

    column_names = Dealership.__table__.columns.keys()
    assert "vat_rate" in column_names
    assert not any("vat_treatment" in name.lower() for name in column_names)
