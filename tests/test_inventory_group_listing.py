"""WP-7 PR-7: group-readable stock listing (ADR-055)."""

import uuid

import pytest

from app.core.errors import NotFoundError
from app.core.pagination import SortPageParams, decode_sort_cursor
from app.core.sorting import SortField
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.group_listing import StockItemGroupRead
from app.inventory.schemas.stock_item import StockItemCreate, StockItemRead
from app.inventory.services.group_listing import list_group_stock_items as _list_group_stock_items
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType

_DEFAULT_SORT = [SortField(api_name="updatedAt", column=StockItem.updated_at, direction="desc", nullable=False)]


def _params(*, limit: int = 50, cursor: str | None = None, sort: list[SortField] | None = None) -> SortPageParams:
    return SortPageParams(
        limit=limit, cursor=decode_sort_cursor(cursor) if cursor else None, sort_fields=sort or _DEFAULT_SORT
    )


def list_group_stock_items(db_session, *, q: str | None = None, params: SortPageParams | None = None, **kwargs):
    """The roster's rows alone, for the tests that only check who is in it."""

    rows, _next_cursor, _total, _is_estimate = _list_group_stock_items(
        db_session, q=q, params=params or _params(), **kwargs
    )
    return rows


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


def _group_with_stock(db_session, labels_by_dealer: dict[str, list[str]]) -> DealerGroup:
    group = DealerGroup(name="Paged group", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()
    for license_number, labels in labels_by_dealer.items():
        dealer = _make_dealership(db_session, group, license_number=license_number)
        for label in labels:
            create_stock_item(
                db_session, tenant_id=dealer.id,
                data=StockItemCreate(vehicle_label=label, condition=StockItemCondition.USED),
                actor_id=uuid.uuid4(),
            )
    return group


def test_group_listing_sorts_server_side(db_session):
    """KAN-152 item 2 — UI/UX Core Principles: every column sortable
    server-side; the group grid used to receive one fixed order."""

    group = _group_with_stock(db_session, {"ZH-1": ["Car 1", "Car 2"], "ZH-2": ["Car 3"]})
    ascending = [SortField(api_name="stockNumber", column=StockItem.stock_number, direction="asc", nullable=False)]
    descending = [SortField(api_name="stockNumber", column=StockItem.stock_number, direction="desc", nullable=False)]

    asc_rows, _, _, _ = _list_group_stock_items(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
        q=None, params=_params(sort=ascending),
    )
    desc_rows, _, _, _ = _list_group_stock_items(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
        q=None, params=_params(sort=descending),
    )
    asc_numbers = [item.stock_number for item, _ in asc_rows]
    assert asc_numbers == sorted(asc_numbers)
    assert [item.stock_number for item, _ in desc_rows] == list(reversed(asc_numbers))


def test_group_listing_walks_every_row_once_by_cursor(db_session):
    """KAN-152 item 2 — cursor-based lazy loading: the group no longer
    arrives as one response."""

    group = _group_with_stock(db_session, {"ZH-1": ["A1", "A2", "A3"], "ZH-2": ["B1", "B2"]})

    seen: list[str] = []
    dealership_labels: set[str] = set()
    cursor = None
    pages = 0
    while True:
        rows, cursor, total, is_estimate = _list_group_stock_items(
            db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
            q=None, params=_params(limit=2, cursor=cursor),
        )
        pages += 1
        assert len(rows) <= 2
        assert (total, is_estimate) == (5, False)
        seen.extend(item.vehicle_label for item, _ in rows)
        dealership_labels.update(dealership.legal_name for _, dealership in rows)
        if cursor is None:
            break

    assert pages == 3
    assert sorted(seen) == ["A1", "A2", "A3", "B1", "B2"]
    assert dealership_labels == {"Garage ZH-1", "Garage ZH-2"}


def test_group_listing_searches_server_side(db_session):
    group = _group_with_stock(db_session, {"ZH-1": ["Škoda Octavia", "VW Golf"], "ZH-2": ["Škoda Superb"]})

    rows, next_cursor, total, _ = _list_group_stock_items(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
        q="koda", params=_params(),
    )
    assert {item.vehicle_label for item, _ in rows} == {"Škoda Octavia", "Škoda Superb"}
    assert (next_cursor, total) == (None, 2)
