"""WP-8 PR-8: line items (S-D14), the two-step build -> review finalize
transition (ADR-063), and the API surface for both.
"""

import uuid
from decimal import Decimal

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError, NotFoundError
from app.customer.schemas.customer import CustomerCreate, CustomerEmailCreate
from app.customer.services.customer import create_customer
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.pricing import OptionInput
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pricing import set_options
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.models.offer import OfferStatus
from app.sales.schemas.line_item import LineItemAccessoryInput, LineItemFactoryOptionPatch, LineItemsReplaceRequest
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.line_items import replace_line_items
from app.sales.services.offer import create_offer, finalize_offer, update_offer


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
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _stock_item(db_session, tenant_id, *, condition=StockItemCondition.NEW, with_option=True):
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="VW Golf GTI", condition=condition),
        actor_id=uuid.uuid4(),
    )
    options = [OptionInput(code="LED", label="LED-Scheinwerfer", price=Decimal("800.00"))] if with_option else []
    if condition in (StockItemCondition.NEW, StockItemCondition.TAGESZ, StockItemCondition.DEMO):
        item = set_options(db_session, item=item, base_price=Decimal("38000.00"), options=options, actor_id=uuid.uuid4())
    else:
        item = set_options(db_session, item=item, base_price=Decimal("28000.00"), options=[], actor_id=uuid.uuid4())
    item = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Auto AG", supplier_is_vat_registered=True, purchase_price=Decimal("30000.00"), purchase_date="2026-01-01"
        ),
        actor_id=uuid.uuid4(),
    )
    return item


def _customer(db_session, group_id):
    return create_customer(
        db_session,
        group_id=group_id,
        data=CustomerCreate(
            customer_type="individual", language="de", first_name="Didier", last_name="Perrin",
            emails=[CustomerEmailCreate(email_type="personal", email_address="didier@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(),
    )


def _offer_with_vehicle(db_session, tenant_id, item, *, group_id=None):
    group_id = group_id or uuid.uuid4()
    customer = _customer(db_session, group_id)
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    return update_offer(
        db_session,
        offer=offer,
        group_id=group_id,
        data=OfferUpdate(
            customer_id=customer.id, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label
        ),
        actor_id=uuid.uuid4(),
    )


# --- accessories (S-D14) -----------------------------------------------------


def test_replace_line_items_adds_accessory_and_recomputes_totals(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    updated = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[LineItemAccessoryInput(code="MATS", label="Fussmatten", unit_price=Decimal("150.00"))]
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.accessories_total == Decimal("150.00")
    assert updated.gross_price == Decimal("38150.00")


def test_replace_line_items_removes_accessory_not_resubmitted(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    offer = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[LineItemAccessoryInput(code="MATS", label="Fussmatten", unit_price=Decimal("150.00"))]
        ),
        actor_id=uuid.uuid4(),
    )
    accessory_id = next(
        li.id for li in db_session.query(SalesLineItem).filter_by(offer_id=offer.id, kind=LineItemKind.ACCESSORY).all()
    )

    updated = replace_line_items(db_session, offer=offer, data=LineItemsReplaceRequest(accessories=[]), actor_id=uuid.uuid4())

    assert updated.accessories_total == Decimal(0)
    remaining = db_session.query(SalesLineItem).filter_by(id=accessory_id).first()
    assert remaining is None


def test_replace_line_items_updates_an_existing_accessory_by_id(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    offer = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[LineItemAccessoryInput(code="MATS", label="Fussmatten", unit_price=Decimal("150.00"))]
        ),
        actor_id=uuid.uuid4(),
    )
    accessory = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, kind=LineItemKind.ACCESSORY).one()

    updated = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[
                LineItemAccessoryInput(id=accessory.id, code="MATS", label="Fussmatten Premium", unit_price=Decimal("220.00"))
            ]
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.accessories_total == Decimal("220.00")
    row = db_session.query(SalesLineItem).filter_by(id=accessory.id).one()
    assert row.label == "Fussmatten Premium"


# --- factory options (individually deselectable) -----------------------------


def test_toggle_factory_option_excluded_recomputes_options_total(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=True)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    option = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, kind=LineItemKind.FACTORY_OPTION).one()
    assert offer.options_total == Decimal("800.00")

    updated = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            factory_options=[LineItemFactoryOptionPatch(id=option.id, included=False)]
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.options_total == Decimal(0)
    row = db_session.query(SalesLineItem).filter_by(id=option.id).one()
    assert row.included is False
    assert row.code == "LED"  # deselected, never deleted — the row (and history) stays


def test_replace_line_items_unknown_factory_option_id_is_404(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=True)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    with pytest.raises(NotFoundError):
        replace_line_items(
            db_session,
            offer=offer,
            data=LineItemsReplaceRequest(
                factory_options=[LineItemFactoryOptionPatch(id=uuid.uuid4(), included=False)]
            ),
            actor_id=uuid.uuid4(),
        )


# --- suppressed-with-reason discounts on used vehicles ------------------------


def test_discount_on_used_vehicle_line_item_requires_a_reason(db_session):
    """Stock itself refuses to itemise factory options on used stock
    (app.inventory.services.pricing.ITEMIZABLE_CONDITIONS) — so a used
    vehicle's frozen snapshot never carries any; the practical case this
    rule protects is an ACCESSORY line on a used vehicle, exercised here.
    """

    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, condition=StockItemCondition.USED, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    assert offer.vehicle_snapshot["condition"] == "used"

    with pytest.raises(ConflictError):
        replace_line_items(
            db_session,
            offer=offer,
            data=LineItemsReplaceRequest(
                accessories=[
                    LineItemAccessoryInput(
                        code="MATS", label="Fussmatten", unit_price=Decimal("150.00"),
                        discount_type="amount", discount_value=Decimal("20.00"),
                    )
                ]
            ),
            actor_id=uuid.uuid4(),
        )


def test_discount_on_used_vehicle_line_item_succeeds_with_a_reason(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, condition=StockItemCondition.USED, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    updated = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[
                LineItemAccessoryInput(
                    code="MATS", label="Fussmatten", unit_price=Decimal("150.00"),
                    discount_type="amount", discount_value=Decimal("20.00"),
                    discount_suppressed_reason="Kulanz — Kunde seit 10 Jahren",
                )
            ]
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.accessories_total == Decimal("130.00")
    row = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, kind=LineItemKind.ACCESSORY).one()
    assert row.discount_suppressed_reason == "Kulanz — Kunde seit 10 Jahren"


def test_discount_on_new_vehicle_line_item_does_not_require_a_reason(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    updated = replace_line_items(
        db_session,
        offer=offer,
        data=LineItemsReplaceRequest(
            accessories=[
                LineItemAccessoryInput(
                    code="MATS", label="Fussmatten", unit_price=Decimal("150.00"),
                    discount_type="amount", discount_value=Decimal("20.00"),
                )
            ]
        ),
        actor_id=uuid.uuid4(),
    )

    assert updated.accessories_total == Decimal("130.00")


def test_replace_line_items_refused_once_offer_is_no_longer_draft(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    finalized = finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        replace_line_items(
            db_session,
            offer=finalized,
            data=LineItemsReplaceRequest(accessories=[LineItemAccessoryInput(code="X", label="X", unit_price=Decimal(1))]),
            actor_id=uuid.uuid4(),
        )


# --- finalize (ADR-063: build, then review, then this) ------------------------


def test_finalize_offer_requires_customer_and_vehicle(db_session):
    dealership = _make_dealership(db_session)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())


def test_finalize_offer_transitions_draft_to_open(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)

    finalized = finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())

    assert finalized.status == OfferStatus.OPEN


def test_finalize_offer_refuses_from_a_non_draft_status(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())


def test_finalized_offer_can_no_longer_be_autosave_edited(db_session):
    dealership = _make_dealership(db_session)
    item = _stock_item(db_session, dealership.id, with_option=False)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    finalize_offer(db_session, offer=offer, actor_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        update_offer(
            db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(vehicle_label="Changed"), actor_id=uuid.uuid4()
        )


# --- API surface ---------------------------------------------------------------


def _token(role: AccessRole | None = None) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_finalize_requires_write_capability(client):
    token = _token()
    offer = client.post("/v1/sales/offers", headers=_bearer(_token(AccessRole.SALES))).json()
    response = client.post(
        f"/v1/sales/offers/{offer['id']}/finalize", headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 403, response.text


def test_finalize_via_api_requires_complete_containers(client):
    token = _token(AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    response = client.post(
        f"/v1/sales/offers/{offer['id']}/finalize", headers={**_bearer(token), "If-Match": str(offer["version"])}
    )
    assert response.status_code == 409, response.text
    assert "missingContainers" in response.json()["error"]["details"]


def test_line_items_api_add_and_list_accessory(client):
    token = _token(AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    put = client.put(
        f"/v1/sales/offers/{offer['id']}/line-items",
        json={"accessories": [{"code": "MATS", "label": "Fussmatten", "unitPrice": "150.00", "quantity": 1}]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )
    assert put.status_code == 200, put.text
    assert put.json()["accessoriesTotal"] == "150.00"

    listed = client.get(f"/v1/sales/offers/{offer['id']}/line-items", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["code"] == "MATS"


def test_line_items_api_cross_tenant_get_is_404(client):
    owner = _token(AccessRole.SALES)
    other = _token(AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(owner)).json()

    response = client.get(f"/v1/sales/offers/{offer['id']}/line-items", headers=_bearer(other))
    assert response.status_code == 404, response.text
