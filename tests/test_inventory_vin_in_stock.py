"""KAN-111: a VIN is in a dealership's stock at most once; a car that has
left stock (invoiced, FR-I-12) may come back as a new stock item.

Anto, 2026-09-29: the same VIN may be in pipeline twice, never twice in
stock. Anto, 2026-10-07: a car leaves stock when its customer invoice is
issued; a returning car gets a new stock entry and the sold one stays as
history; a cancelled purchase (storno) still blocks the VIN.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import pipeline as pipeline_service
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.pipeline import handle_sales_contract_confirmed, promote_to_vehicle_mdm
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType

VIN = "WVWZZZ1KZAW000001"


def _make_dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
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


def _in_stock(db_session, tenant_id: uuid.UUID, *, vin: str = VIN) -> StockItem:
    return create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Volkswagen Golf", condition=StockItemCondition.USED, vin=vin),
        actor_id=uuid.uuid4(),
    )


def _pipeline(db_session, tenant_id: uuid.UUID) -> StockItem:
    return create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Volkswagen Golf (Eintausch)", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )


def _sold(db_session, dealership: Dealership) -> StockItem:
    """A car taken into stock, bought and invoiced to a customer — the
    real path (record_purchase, then the invoicing gate), not a column
    poked by hand, so "left stock" here means what FR-I-12 means."""

    item = _in_stock(db_session, dealership.id)
    record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster",
            supplier_is_vat_registered=False,
            purchase_price=Decimal("20000.00"),
            purchase_date=dt.date(2026, 8, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    sold = apply_finance_invoice_issued(
        db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-0001"
    )
    assert sold.left_stock_at is not None
    return sold


def _items_with_vin(db_session, tenant_id: uuid.UUID) -> list[StockItem]:
    return list(db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.vin == VIN)))


def _vin_assigned_events(db_session, item_id: uuid.UUID) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "inventory.pipeline_vehicle.vin_assigned",
                OutboxMessage.aggregate_id == item_id,
            )
        )
    )


# --- A VIN already in stock is refused with a reason -----------------------


def test_creating_a_second_item_with_a_vin_in_stock_is_refused(db_session):
    tenant_id = uuid.uuid4()
    first = _in_stock(db_session, tenant_id)

    with pytest.raises(ConflictError) as refused:
        _in_stock(db_session, tenant_id)

    assert refused.value.details == {
        "reason": "vin_already_in_stock",
        "stockItemId": str(first.id),
        "stockNumber": first.stock_number,
    }
    assert [i.id for i in _items_with_vin(db_session, tenant_id)] == [first.id]


def test_promoting_a_second_trade_in_of_a_car_in_stock_is_refused(db_session):
    """KAN-101 Option B: two contracts may carry the same trade-in, so two
    pipeline items for one car is an expected state. The first promotion
    takes the car into stock; the second is a clear refusal, not a 500."""

    tenant_id = uuid.uuid4()
    for _ in range(2):
        handle_sales_contract_confirmed(
            db_session,
            tenant_id=tenant_id,
            payload={"contractId": str(uuid.uuid4()), "tradeIn": {"vehicleLabel": "Volkswagen Golf"}},
        )
    db_session.commit()
    first, second = db_session.scalars(
        select(StockItem).where(StockItem.tenant_id == tenant_id).order_by(StockItem.stock_number)
    ).all()

    promote_to_vehicle_mdm(db_session, item=first, vin=VIN)
    db_session.commit()

    with pytest.raises(ConflictError) as refused:
        promote_to_vehicle_mdm(db_session, item=second, vin=VIN)

    assert refused.value.details == {
        "reason": "vin_already_in_stock",
        "stockItemId": str(first.id),
        "stockNumber": first.stock_number,
    }
    db_session.rollback()
    db_session.refresh(second)
    assert second.lifecycle_status == LifecycleStatus.PIPELINE
    assert second.vin is None
    assert second.vehicle_id is None
    assert _vin_assigned_events(db_session, second.id) == []


def test_the_same_vin_in_another_dealership_is_not_a_duplicate(db_session):
    _in_stock(db_session, uuid.uuid4())
    _in_stock(db_session, uuid.uuid4())  # a different tenant: no conflict


# --- A car that left stock may come back -----------------------------------


def test_a_sold_car_can_be_taken_into_stock_again_as_a_new_item(db_session):
    dealership = _make_dealership(db_session)
    sold = _sold(db_session, dealership)

    returned = _in_stock(db_session, dealership.id)

    assert returned.id != sold.id
    assert returned.stock_number != sold.stock_number
    assert returned.lifecycle_status == LifecycleStatus.IN_STOCK
    db_session.refresh(sold)
    assert sold.vin == VIN  # the sold entry stays as history, untouched
    assert sold.left_stock_at is not None


def test_a_sold_car_coming_back_as_a_trade_in_can_be_promoted(db_session):
    dealership = _make_dealership(db_session)
    _sold(db_session, dealership)
    trade_in = _pipeline(db_session, dealership.id)

    promoted = promote_to_vehicle_mdm(db_session, item=trade_in, vin=VIN)
    db_session.commit()

    assert promoted.lifecycle_status == LifecycleStatus.IN_STOCK
    assert promoted.vin == VIN
    assert len(_vin_assigned_events(db_session, trade_in.id)) == 1


def test_once_a_sold_car_is_back_in_stock_it_blocks_its_vin_again(db_session):
    dealership = _make_dealership(db_session)
    _sold(db_session, dealership)
    returned = _in_stock(db_session, dealership.id)

    with pytest.raises(ConflictError) as refused:
        _in_stock(db_session, dealership.id)

    assert refused.value.details["stockItemId"] == str(returned.id)


# --- Two writers racing past the check: still a 409, never a 500 -----------


def test_a_racing_create_is_refused_by_the_index_with_the_same_reason(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    first = _in_stock(db_session, tenant_id)
    # The other writer committed after this one looked: the check sees
    # nothing, the unique index is what refuses.
    monkeypatch.setattr(stock_item_service, "_check_vin_not_in_stock", lambda *a, **k: None)

    with pytest.raises(ConflictError) as refused:
        _in_stock(db_session, tenant_id)

    assert refused.value.details["reason"] == "vin_already_in_stock"
    assert refused.value.details["stockItemId"] == str(first.id)


def test_a_racing_promotion_is_refused_by_the_index_with_the_same_reason(db_session, monkeypatch):
    tenant_id = uuid.uuid4()
    first = _in_stock(db_session, tenant_id)
    trade_in = _pipeline(db_session, tenant_id)
    monkeypatch.setattr(pipeline_service, "_check_vin_not_in_stock", lambda *a, **k: None)

    with pytest.raises(ConflictError) as refused:
        promote_to_vehicle_mdm(db_session, item=trade_in, vin=VIN)

    assert refused.value.details["reason"] == "vin_already_in_stock"
    assert refused.value.details["stockItemId"] == str(first.id)
    # Only the promotion's own savepoint was rolled back: the item is a
    # pipeline item again, in memory as in the database.
    assert trade_in.lifecycle_status == LifecycleStatus.PIPELINE
    assert trade_in.vin is None
    assert _vin_assigned_events(db_session, trade_in.id) == []


# --- Over HTTP -------------------------------------------------------------


def _bearer() -> dict[str, str]:
    tid = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


def test_post_with_a_vin_in_stock_returns_409_with_the_reason(client):
    headers = _bearer()
    body = {"vehicleLabel": "Volkswagen Golf", "condition": "used", "vin": VIN}
    first = client.post("/v1/inventory/stock-items", json=body, headers=headers)
    assert first.status_code == 201, first.text

    second = client.post("/v1/inventory/stock-items", json=body, headers=headers)

    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"] == {
        "reason": "vin_already_in_stock",
        "stockItemId": first.json()["id"],
        "stockNumber": first.json()["stockNumber"],
    }
