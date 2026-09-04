"""WP-8 PR-3: vehicle snapshot (ADR-041) + pricing.build_up()."""

import uuid
from decimal import Decimal

from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.pricing import OptionInput
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pricing import set_options
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.offer import create_offer, update_offer


def _make_dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(),
        dealer_group_id=group.id,
        legal_name="Garage AG",
        dealer_license_number="ZH-1",
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        tax_id="CHE-123.456.789",
        vat_rate=Decimal("8.10"),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _priced_stock_item(db_session, tenant_id):
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="BMW 3er 320d xDrive Touring M Sport", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    item = set_options(
        db_session,
        item=item,
        base_price=Decimal("50000.00"),
        options=[OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"))],
        actor_id=uuid.uuid4(),
    )
    item = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Auto AG",
            supplier_is_vat_registered=True,
            purchase_price=Decimal("42000.00"),
            purchase_date="2026-01-01",
        ),
        actor_id=uuid.uuid4(),
    )
    return item


def test_selecting_a_stock_vehicle_freezes_snapshot_and_builds_pricing(db_session):
    dealership = _make_dealership(db_session)
    tenant_id = dealership.id
    item = _priced_stock_item(db_session, tenant_id)
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    assert updated.vehicle_snapshot_frozen_at is not None
    assert updated.base_price == Decimal("50000.00")
    assert updated.options_total == Decimal("800.00")
    assert updated.list_price == Decimal("50800.00")
    assert updated.accessories_total == Decimal("0.00")
    assert updated.total_before_discount == Decimal("50800.00")
    assert updated.discount_amount == Decimal(0)
    assert updated.gross_price == Decimal("50800.00")
    assert updated.cost_basis == Decimal("42000.00")
    assert updated.margin == Decimal("8800.00")

    line_items = db_session.query(SalesLineItem).filter_by(offer_id=updated.id).all()
    assert len(line_items) == 1
    assert line_items[0].kind == LineItemKind.FACTORY_OPTION
    assert line_items[0].code == "LED"


def _priced_stock_item_with_landed_cost(db_session, tenant_id):
    """KAN-25 fixture: a used car bought from a private individual (no
    real input VAT paid), so the fiktiver Vorsteuerabzug applies and
    landed cost genuinely differs from the raw purchase price.
    """

    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Audi A4 2.0 TDI Avant", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    item = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Privat, Hans Muster",
            supplier_is_vat_registered=False,
            purchase_price=Decimal("30000.00"),
            purchase_date="2026-01-01",
            landed_cost=Decimal("30800.00"),  # purchase price + prep/transport
        ),
        actor_id=uuid.uuid4(),
    )
    return item


def test_stock_item_pricing_returns_landed_cost_and_notional_input_tax(db_session):
    """KAN-25 exit criterion 1: get_stock_item_pricing returns landed cost
    and the notional input tax fields, correctly signed — read straight
    off the stock item, no sign applied at this layer.
    """

    from app.inventory.services.pricing import get_stock_item_pricing

    dealership = _make_dealership(db_session)
    item = _priced_stock_item_with_landed_cost(db_session, dealership.id)

    pricing = get_stock_item_pricing(db_session, tenant_id=dealership.id, stock_item_id=item.id)

    assert pricing["landedCost"] == item.landed_cost == Decimal("30800.00")
    assert pricing["notionalInputTaxApplicable"] is True
    assert pricing["notionalInputTaxRate"] == dealership.vat_rate
    assert pricing["notionalInputTaxAmount"] == item.notional_input_tax_amount
    assert pricing["notionalInputTaxAmount"] > 0  # a credit, stored positive — never pre-negated


def test_landed_cost_and_notional_input_tax_survive_acquisition_to_offer(db_session):
    """KAN-25 exit criterion 3: the value read from a Sales offer equals
    the value persisted at acquisition — the acquisition-to-offer hop,
    end to end, in one test rather than two units that never cross it
    (the trap the ticket named).
    """

    dealership = _make_dealership(db_session)
    item = _priced_stock_item_with_landed_cost(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    snapshot = updated.vehicle_snapshot
    assert Decimal(snapshot["landedCost"]) == item.landed_cost
    assert snapshot["notionalInputTaxApplicable"] == item.notional_input_tax_applicable
    assert Decimal(snapshot["notionalInputTaxRate"]) == item.notional_input_tax_rate
    assert Decimal(snapshot["notionalInputTaxAmount"]) == item.notional_input_tax_amount


def test_margin_uses_landed_cost_net_of_the_notional_input_tax_credit(db_session):
    """KAN-25's sign test: the credit REDUCES cost basis. A sign error
    (adding instead of subtracting) would silently overstate cost and
    understate margin by twice the credit — invisible until it's on a
    VAT return, per the ticket's own warning.
    """

    dealership = _make_dealership(db_session)
    item = _priced_stock_item_with_landed_cost(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    expected_cost_basis = item.landed_cost - item.notional_input_tax_amount
    assert updated.cost_basis == expected_cost_basis
    assert updated.cost_basis < item.landed_cost  # the credit reduced it, not inflated it
    assert updated.margin == updated.gross_price - expected_cost_basis


def test_cost_basis_falls_back_to_purchase_price_when_no_landed_cost_recorded(db_session):
    """Backward compatible: WP-7 PR-3 predates this fix, and plenty of
    stock items were priced before landed_cost was ever entered. Same
    fixture and assertion as
    test_selecting_a_stock_vehicle_freezes_snapshot_and_builds_pricing —
    proving this fix did not change that behaviour.
    """

    dealership = _make_dealership(db_session)
    item = _priced_stock_item(db_session, dealership.id)
    assert item.landed_cost is None
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    assert updated.cost_basis == Decimal("42000.00")
    assert updated.margin == Decimal("8800.00")


def test_percent_discount_resolves_against_total_before_discount(db_session):
    dealership = _make_dealership(db_session)
    tenant_id = dealership.id
    item = _priced_stock_item(db_session, tenant_id)
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    updated = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(discount_type="percent", discount_value=Decimal(10)),
        actor_id=uuid.uuid4(),
    )

    assert updated.discount_amount == Decimal("5080.00")
    assert updated.gross_price == Decimal("45720.00")


def test_amount_discount_is_used_as_is(db_session):
    dealership = _make_dealership(db_session)
    tenant_id = dealership.id
    item = _priced_stock_item(db_session, tenant_id)
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )

    updated = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(discount_type="amount", discount_value=Decimal(2000)),
        actor_id=uuid.uuid4(),
    )
    assert updated.discount_amount == Decimal(2000)
    assert updated.gross_price == Decimal("48800.00")


def test_manual_configuration_has_no_cost_basis_or_margin(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    updated = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(
            vehicle_source="manual", vehicle_label="Volkswagen Käfer", manual_vehicle_condition="used",
            manual_base_price=Decimal("18000.00"),
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.base_price == Decimal("18000.00")
    assert updated.gross_price == Decimal("18000.00")
    assert updated.cost_basis is None
    assert updated.margin is None


def test_reselecting_the_same_vehicle_does_not_refreeze(db_session):
    """Confirmed live, verbatim: "a later catalogue correction never
    changes an existing offer." """

    dealership = _make_dealership(db_session)
    tenant_id = dealership.id
    item = _priced_stock_item(db_session, tenant_id)
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    first = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    frozen_at = first.vehicle_snapshot_frozen_at

    # A later catalogue correction to the stock item itself...
    item.base_price = Decimal("99999.00")
    db_session.commit()

    # ...must not change anything on an offer already frozen for it, even
    # touched again via an unrelated field.
    second = update_offer(
        db_session, offer=first, group_id=uuid.uuid4(), data=OfferUpdate(leasing_term_months=36), actor_id=uuid.uuid4()
    )
    assert second.vehicle_snapshot_frozen_at == frozen_at
    assert second.base_price == Decimal("50000.00")


def test_switching_to_a_different_stock_item_refreezes(db_session):
    dealership = _make_dealership(db_session)
    tenant_id = dealership.id
    item_a = _priced_stock_item(db_session, tenant_id)
    item_b = _priced_stock_item(db_session, tenant_id)
    item_b.base_price = Decimal("30000.00")
    item_b.list_price = Decimal("30000.00")
    db_session.commit()

    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item_a.id, vehicle_label=item_a.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    assert offer.base_price == Decimal("50000.00")

    switched = update_offer(
        db_session,
        offer=offer,
        group_id=uuid.uuid4(),
        data=OfferUpdate(stock_item_id=item_b.id, vehicle_label=item_b.vehicle_label),
        actor_id=uuid.uuid4(),
    )
    assert switched.base_price == Decimal("30000.00")


def test_margin_has_no_group_scoped_reader_anywhere(client):
    """ADR-029/049 — structural, not a filtered column: there is no
    group-scoped endpoint for sales_offer/sales_contract/sales_deal at
    all."""

    schema = client.app.openapi()
    group_paths = [p for p in schema["paths"] if "/sales/" in p and "group" in p.lower()]
    assert group_paths == []
