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
