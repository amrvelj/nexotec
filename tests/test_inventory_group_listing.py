"""WP-7 PR-7: group-readable stock listing (ADR-055)."""

import uuid

import pytest

from app.core.errors import NotFoundError
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.group_listing import StockItemGroupRead
from app.inventory.schemas.stock_item import StockItemCreate, StockItemRead
from app.inventory.services.group_listing import list_group_stock_items
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType


def _make_dealership(db_session, group: DealerGroup, *, license_number: str) -> Dealership:
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name=f"Garage {license_number}",
        dealer_license_number=license_number,
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        tax_id=f"CHE-{license_number}.789",
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def test_group_listing_excludes_commercial_fields_by_name():
    """ADR-055 — asserted by name against the schema itself, not just
    'fewer columns than the tenant grid.'"""

    field_names = set(StockItemGroupRead.model_fields.keys())
    forbidden = {
        "effective_price", "landed_cost", "notional_input_tax_applicable", "notional_input_tax_rate",
        "notional_input_tax_amount", "purchase_price", "purchase_invoice_ref", "supplier_name",
        "is_invoiceable",
        # WP-7 PR-9
        "base_price", "valuation_ref_id", "valuation_ref_amount", "valuation_ref_valued_at", "valuation_ref_source",
    }
    assert not (field_names & forbidden), f"Group projection leaks entity-private fields: {field_names & forbidden}"
    # And it genuinely is a distinct schema, not StockItemRead reused.
    assert field_names != set(StockItemRead.model_fields.keys())


def test_group_listing_returns_stock_across_sibling_dealerships(db_session):
    group = DealerGroup(name="Multi-site group", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()
    dealer_a = _make_dealership(db_session, group, license_number="ZH-1")
    dealer_b = _make_dealership(db_session, group, license_number="ZH-2")

    create_stock_item(
        db_session, tenant_id=dealer_a.id,
        data=StockItemCreate(vehicle_label="Car at dealer A", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    create_stock_item(
        db_session, tenant_id=dealer_b.id,
        data=StockItemCreate(vehicle_label="Car at dealer B", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )

    rows = list_group_stock_items(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True
    )
    labels = {item.vehicle_label for item, _dealership in rows}
    assert labels == {"Car at dealer A", "Car at dealer B"}


def test_group_listing_404s_when_group_read_not_enabled(db_session):
    group = DealerGroup(name="Read-disabled group", group_read_enabled=False)
    db_session.add(group)
    db_session.commit()

    with pytest.raises(NotFoundError):
        list_group_stock_items(
            db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True
        )


def test_group_listing_404s_never_403s_for_a_different_group(db_session):
    own_group = DealerGroup(name="My group", group_read_enabled=True)
    other_group = DealerGroup(name="Someone else's group", group_read_enabled=True)
    db_session.add_all([own_group, other_group])
    db_session.commit()

    with pytest.raises(NotFoundError):
        list_group_stock_items(
            db_session, principal_group_id=own_group.id, requested_group_id=other_group.id, is_authorized=lambda: True
        )
