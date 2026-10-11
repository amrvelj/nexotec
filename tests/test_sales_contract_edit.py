"""KAN-67 (ADR-083): a pending contract is completed or adjusted — vehicle,
price build-up, line items, trade-in — through the offer's own services,
and frozen once confirmed.

Exit criteria covered here: a contract born from a customer reaches
`confirmed` with a vehicle and a price (2, at service and API level — the
UI and staging are verified outside this file); generation of its document
works once it is complete (3, the refusal itself is in
test_sales_documents.py); the KAN-66 guard stays green (5, in
test_sales_lifecycle_reservation.py, unchanged); the migration's backfill
of pending offer-born contracts (4's "accounted for" for existing data).
"""

import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, null, select, update

from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.db import SessionLocal
from app.inventory.models.stock_item import ReservationState, StockItem, StockItemCondition
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.schemas.contract import ContractUpdate
from app.sales.schemas.line_item import LineItemAccessoryInput, LineItemFactoryOptionPatch, LineItemsReplaceRequest
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import (
    cancel_contract,
    confirm_contract,
    create_contract,
    update_contract,
)
from app.sales.services.document import generate_contract_document
from app.sales.services.line_items import list_line_items, replace_line_items
from app.sales.services.offer import update_offer
from app.sales.services.priced_deal import compute_containers
from app.sales.services.trade_in import attach_trade_in_valuation, set_trade_in
from app.valuation.models.valuation import ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from tests.test_configurator_hosts import _build_configuration, _record_configuration
from tests.test_sales_lifecycle_reservation import _customer, _dealership, _session_factory
from tests.test_sales_offer_refinements import _offer_with_vehicle, _stock_item


def _customer_born_contract(db_session, dealership_id: uuid.UUID, group_id: uuid.UUID) -> SalesContract:
    """The KAN-58 entry point: a contract with a customer and nothing else."""

    customer = _customer(db_session, group_id)
    return create_contract(
        db_session, tenant_id=dealership_id, offer=None, actor_id=uuid.uuid4(), customer_id=customer.id,
        group_id=group_id,
    )


def _edit(db_session, contract: SalesContract, **fields) -> SalesContract:
    return update_contract(db_session, contract=contract, data=ContractUpdate(**fields), actor_id=uuid.uuid4())


# --- a customer-born contract can be completed and confirmed ----------------------


def test_a_customer_born_contract_takes_a_stock_car_priced_from_the_frozen_snapshot(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)  # 38'000 base + LED option 800, bought for 30'000
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    version = contract.version

    contract = _edit(db_session, contract, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label)

    assert contract.vehicle_snapshot is not None and contract.vehicle_snapshot["basePrice"] == "38000.00"
    assert contract.vehicle_snapshot_frozen_at is not None
    assert contract.base_price == Decimal("38000.00")
    assert contract.options_total == Decimal("800.00")
    assert contract.gross_price == Decimal("38800.00")
    assert contract.payable == Decimal("38800.00")
    assert contract.cost_basis is not None and contract.margin == contract.gross_price - contract.cost_basis
    assert contract.version == version + 1
    options = list_line_items(db_session, deal=contract)
    assert [(o.kind, o.code, o.contract_id, o.offer_id) for o in options] == [
        (LineItemKind.FACTORY_OPTION, "LED", contract.id, None)
    ]


def test_a_customer_born_contract_with_a_manual_car_confirms(db_session, engine):
    """Exit criterion 2 at service level: born from a customer, completed
    with a vehicle and a price, confirmed — and the confirmed event carries
    the price build-up the edit produced."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _customer_born_contract(db_session, dealership.id, group_id)

    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = _edit(db_session, contract, discount_type="amount", discount_value=Decimal("500.00"))
    assert contract.gross_price == Decimal("17500.00")

    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    assert contract.status == ContractStatus.CONFIRMED
    payload = db_session.scalars(
        select(OutboxMessage.payload).where(
            OutboxMessage.event_type == "sales.contract.confirmed", OutboxMessage.aggregate_id == contract.id
        )
    ).one()
    assert payload["pricingSnapshot"]["grossPrice"] == "17500.00"
    assert payload["pricingSnapshot"]["discountAmount"] == "500.00"
    assert payload["manualConfiguration"] == {"vehicleLabel": "Volkswagen Käfer", "condition": "used"}


def test_a_customer_born_contract_with_a_stock_car_confirms_and_reserves_it(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id, with_option=False)
    contract = _customer_born_contract(db_session, dealership.id, group_id)
    contract = _edit(db_session, contract, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label)

    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    assert contract.status == ContractStatus.CONFIRMED and contract.reservation_id is not None
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).reservation_state == ReservationState.RESERVED


def test_a_configured_new_car_on_a_contract_prices_its_configuration_lines(db_session):
    dealership = _dealership(db_session)
    config = _build_configuration(db_session, dealership.id)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())

    contract = _edit(db_session, contract, configuration_id=config.id)

    assert contract.vehicle_source == "manual" and contract.configuration_id == config.id
    assert contract.manual_vehicle_condition == "new"
    assert contract.manual_base_price == Decimal("52900.00")
    assert contract.vehicle_snapshot["configurationId"] == str(config.id)
    # Selected options and the colour surcharge, as on the offer (FR-C-12).
    assert contract.options_total == Decimal("1250.00") + Decimal("1490.00") + Decimal("990.00")
    assert contract.gross_price == Decimal("52900.00") + contract.options_total


def test_a_contract_refuses_a_record_mode_configuration(db_session):
    dealership = _dealership(db_session)
    record = _record_configuration(db_session, dealership.id)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())

    with pytest.raises(UnprocessableEntityError) as refused:
        _edit(db_session, contract, configuration_id=record.id)
    assert refused.value.details["reason"] == "configuration_mode_not_allowed"


def test_a_contract_takes_accessories_and_option_changes_through_its_own_line_items(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    contract = _edit(db_session, contract, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label)
    led = list_line_items(db_session, deal=contract)[0]

    contract = replace_line_items(
        db_session,
        deal=contract,
        data=LineItemsReplaceRequest(
            accessories=[LineItemAccessoryInput(code="MATS", label="Fussmatten", unit_price=Decimal("150.00"))],
            factory_options=[LineItemFactoryOptionPatch(id=led.id, included=False)],
        ),
        actor_id=uuid.uuid4(),
    )

    assert contract.options_total == Decimal(0)
    assert contract.accessories_total == Decimal("150.00")
    assert contract.gross_price == Decimal("38150.00")


def _valuation(db_session, dealership_id: uuid.UUID, group_id: uuid.UUID, vin: str, final_offer: Decimal):
    return create_valuation(
        db_session, tenant_id=dealership_id, group_id=group_id,
        data=ValuationCreate(vin=vin, source=ValuationSource.MANUAL, final_offer=final_offer),
        actor_id=uuid.uuid4(),
    )


def _manual_car(db_session, contract: SalesContract, price: str = "18000.00") -> SalesContract:
    return _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal(price),
    )


def test_a_trade_in_on_a_contract_reduces_what_the_customer_pays(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))

    contract = set_trade_in(
        db_session, deal=contract, group_id=group_id, vin="WVWZZZ1KZAW000001", plate=None, canton=None,
        vehicle_label="Opel Corsa", customer_id=None, actor_id=uuid.uuid4(),
    )
    assert (contract.trade_in_vin, contract.trade_in_label) == ("WVWZZZ1KZAW000001", "Opel Corsa")
    valuation = _valuation(db_session, dealership.id, group_id, "WVWZZZ1KZAW000001", Decimal("6000.00"))

    contract = attach_trade_in_valuation(db_session, deal=contract, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert contract.trade_in_valuation_id == valuation.id
    assert contract.trade_in_value == Decimal("6000.00")
    assert contract.gross_price == Decimal("18000.00")
    assert contract.payable == Decimal("12000.00")


# --- CHF 0.00 is a price (Anto, 2026-10-10) ---------------------------------------


def test_a_car_without_a_price_entered_is_signed_at_chf_0(db_session, engine):
    """Product owner's ruling: build_up() turns a car with no base price into
    CHF 0.00, and that may be confirmed and printed — only an empty price is
    missing_price. The pricing container still flags it as in progress."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _customer_born_contract(db_session, dealership.id, group_id)
    contract = _edit(db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
                     manual_vehicle_condition="used")
    assert contract.gross_price == Decimal("0.00")
    assert {c.id: c.status for c in compute_containers(contract)}["pricing"] == "in_progress"

    assert generate_contract_document(db_session, contract=contract, actor_id=None).version == 1
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    assert contract.status == ContractStatus.CONFIRMED


# --- frozen on confirm, closed on cancel ---------------------------------------


def test_a_confirmed_contract_cannot_be_edited(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _customer_born_contract(db_session, dealership.id, group_id)
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    valuation = _valuation(db_session, dealership.id, group_id, "WVWZZZ1KZAW777777", Decimal("9000.00"))

    for edit in (
        lambda: _edit(db_session, contract, discount_type="amount", discount_value=Decimal("1000.00")),
        lambda: replace_line_items(
            db_session, deal=contract, data=LineItemsReplaceRequest(), actor_id=uuid.uuid4()
        ),
        lambda: set_trade_in(
            db_session, deal=contract, group_id=group_id, vin="WVWZZZ1KZAW000001", plate=None, canton=None,
            vehicle_label="Opel Corsa", customer_id=None, actor_id=uuid.uuid4(),
        ),
        lambda: attach_trade_in_valuation(  # review finding: this route skipped the freeze
            db_session, deal=contract, valuation_id=valuation.id, actor_id=uuid.uuid4()
        ),
    ):
        with pytest.raises(ConflictError) as refused:
            edit()
        assert refused.value.details == {"reason": "contract_not_pending", "status": "confirmed"}

    db_session.refresh(contract)
    assert contract.gross_price == Decimal("18000.00")
    assert (contract.payable, contract.trade_in_valuation_id) == (Decimal("18000.00"), None)


def test_a_cancelled_contract_cannot_be_edited(db_session):
    dealership = _dealership(db_session)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    contract = cancel_contract(db_session, contract=contract, reason="Kunde verzichtet", actor_id=uuid.uuid4())

    with pytest.raises(ConflictError) as refused:
        _edit(db_session, contract, manual_base_price=Decimal("1.00"))
    assert refused.value.details["reason"] == "contract_not_pending"


# --- a contract from an offer -------------------------------------------------------


def test_a_contract_from_an_offer_carries_its_pricing_inputs_and_lines(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(discount_type="percent", discount_value=Decimal(5)), actor_id=uuid.uuid4(),
    )

    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())

    assert contract.vehicle_snapshot == offer.vehicle_snapshot
    assert (contract.discount_type, contract.discount_value) == ("percent", Decimal("5.00"))
    assert contract.total_before_discount == offer.total_before_discount
    assert contract.cost_basis == offer.cost_basis
    contract_lines = list_line_items(db_session, deal=contract)
    assert [(li.code, li.contract_id, li.offer_id) for li in contract_lines] == [("LED", contract.id, None)]
    # The offer keeps its own lines: it stays the record of what was quoted.
    assert [li.code for li in list_line_items(db_session, deal=offer)] == ["LED"]


def test_editing_a_contract_from_an_offer_rebuilds_the_same_price_and_leaves_the_offer_alone(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    quoted = offer.gross_price

    contract = _edit(db_session, contract, discount_type="amount", discount_value=Decimal("800.00"))

    # Same snapshot and lines, so the only change is the new discount — the
    # car is not re-frozen from live stock data (ADR-041).
    assert contract.gross_price == quoted - Decimal("800.00")
    db_session.refresh(offer)
    assert offer.gross_price == quoted
    assert offer.discount_value is None


def test_the_deal_grid_shows_the_edited_price(db_session):
    dealership = _dealership(db_session)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())

    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )

    deal = db_session.get(SalesDeal, contract.id)
    assert (deal.vehicle_label, deal.gross_price) == ("Volkswagen Käfer", Decimal("18000.00"))


def test_completing_a_contract_lets_its_document_be_generated(db_session):
    dealership = _dealership(db_session)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )

    document = generate_contract_document(db_session, contract=contract, actor_id=None)

    assert document.version == 1
    assert "Volkswagen Käfer" in str(document.content_definition)


def test_editing_publishes_no_event(db_session):
    """ADR-083: nothing outside Sales acts on a pending contract; the
    frozen figures travel in sales.contract.confirmed."""

    dealership = _dealership(db_session)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    before = db_session.scalars(select(OutboxMessage.event_type).where(OutboxMessage.aggregate_id == contract.id)).all()

    _edit(db_session, contract, vehicle_source="manual", vehicle_label="Käfer", manual_base_price=Decimal("1.00"))

    after = db_session.scalars(select(OutboxMessage.event_type).where(OutboxMessage.aggregate_id == contract.id)).all()
    assert after == before == ["sales.contract.created"]


# --- API ------------------------------------------------------------------------


def _token(tenant_id: uuid.UUID, group_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def app_sessions_on_the_test_engine(monkeypatch, engine):
    """confirm_contract reserves on a session of its own (ADR-047), from
    app.db.SessionLocal; over HTTP nothing overrides it."""

    monkeypatch.setitem(SessionLocal.kw, "bind", engine)


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_api_a_customer_born_contract_is_completed_and_confirmed(client, db_session):
    """Exit criterion 2 through the HTTP contract the UI uses."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    headers = _token(dealership.id, group_id)
    item = _stock_item(db_session, dealership.id, with_option=False)
    customer = _customer(db_session, group_id)
    created = client.post("/v1/sales/contracts", json={"customerId": str(customer.id)}, headers=headers).json()
    assert {c["id"]: c["status"] for c in created["containers"]} == {
        "customer": "complete", "vehicle": "not_started", "pricing": "not_started", "trade_in": "not_started",
    }

    patched = client.patch(
        f"/v1/sales/contracts/{created['id']}",
        json={"vehicleSource": "stock", "stockItemId": str(item.id), "vehicleLabel": item.vehicle_label},
        headers={**headers, "If-Match": str(created["version"])},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["grossPrice"] == "38000.00" and body["vehicleCondition"] == "new"
    assert {c["id"]: c["status"] for c in body["containers"]}["pricing"] == "complete"

    discounted = client.patch(
        f"/v1/sales/contracts/{created['id']}",
        json={"discountType": "percent", "discountValue": "10"},
        headers={**headers, "If-Match": str(body["version"])},
    ).json()
    assert discounted["discountAmount"] == "3800.00" and discounted["grossPrice"] == "34200.00"

    confirmed = client.post(
        f"/v1/sales/contracts/{created['id']}/confirm", headers={**headers, "If-Match": str(discounted["version"])}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    frozen = client.patch(
        f"/v1/sales/contracts/{created['id']}",
        json={"discountValue": "20"},
        headers={**headers, "If-Match": str(confirmed.json()["version"])},
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["details"]["reason"] == "contract_not_pending"


def test_api_derived_figures_are_never_taken_from_the_client(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    headers = _token(dealership.id, group_id)
    contract = _customer_born_contract(db_session, dealership.id, group_id)

    response = client.patch(
        f"/v1/sales/contracts/{contract.id}",
        json={"grossPrice": "1.00", "margin": "99999.00"},
        headers={**headers, "If-Match": str(contract.version)},
    )

    db_session.refresh(contract)
    assert response.status_code in (200, 422), response.text
    assert contract.gross_price is None and contract.margin is None


def test_api_patch_needs_if_match_and_refuses_a_stale_version(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    headers = _token(dealership.id, group_id)
    contract = _customer_born_contract(db_session, dealership.id, group_id)

    missing = client.patch(f"/v1/sales/contracts/{contract.id}", json={"vehicleLabel": "x"}, headers=headers)
    stale = client.patch(
        f"/v1/sales/contracts/{contract.id}",
        json={"vehicleLabel": "x"},
        headers={**headers, "If-Match": str(contract.version + 5)},
    )

    assert missing.status_code == 400, missing.text  # the error taxonomy's missing-If-Match answer
    assert stale.status_code == 409, stale.text


def test_api_another_dealerships_contract_is_404_on_every_edit_route(client, db_session):
    dealership = _dealership(db_session)
    contract = _customer_born_contract(db_session, dealership.id, uuid.uuid4())
    other = {**_token(uuid.uuid4(), uuid.uuid4()), "If-Match": str(contract.version)}

    responses = [
        client.patch(f"/v1/sales/contracts/{contract.id}", json={"vehicleLabel": "x"}, headers=other),
        client.post(f"/v1/sales/contracts/{contract.id}/trade-in", json={"vehicleLabel": "x", "vin": "X"}, headers=other),
        client.post(
            f"/v1/sales/contracts/{contract.id}/trade-in/valuation",
            json={"valuationId": str(uuid.uuid4())},
            headers=other,
        ),
        client.get(f"/v1/sales/contracts/{contract.id}/line-items", headers=other),
        client.put(f"/v1/sales/contracts/{contract.id}/line-items", json={}, headers=other),
    ]

    assert [r.status_code for r in responses] == [404] * 5, [r.text for r in responses]


def test_api_contract_line_items_round_trip(client, db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    headers = _token(dealership.id, group_id)
    item = _stock_item(db_session, dealership.id)
    contract = _customer_born_contract(db_session, dealership.id, group_id)
    contract = _edit(db_session, contract, vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label)

    replaced = client.put(
        f"/v1/sales/contracts/{contract.id}/line-items",
        json={"accessories": [{"code": "MATS", "label": "Fussmatten", "unitPrice": "150.00"}]},
        headers={**headers, "If-Match": str(contract.version)},
    )
    listed = client.get(f"/v1/sales/contracts/{contract.id}/line-items", headers=headers)

    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["accessoriesTotal"] == "150.00"
    assert sorted(li["code"] for li in listed.json()["items"]) == ["LED", "MATS"]


# --- the migration's backfill (pending offer-born contracts) ----------------------

_postgres_only_migration = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Migration backfill: migrations target Postgres only (ADR-011); SQLite binds the migration's UUIDs differently.",
)


def _load_migration():
    path = next(Path(__file__).resolve().parents[1].glob("alembic/versions/sales/*_kan_67_contract_pricing_inputs.py"))
    spec = importlib.util.spec_from_file_location("kan67_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _as_before_the_migration(db_session, contract: SalesContract) -> None:
    """A contract created before KAN-67 kept the figures but no inputs or
    lines. SQL NULL, as a freshly added column holds — the ORM would store
    `None` in a JSON column as JSON `null`."""

    db_session.execute(
        update(SalesContract)
        .where(SalesContract.id == contract.id)
        .values(
            manual_base_price=None, vehicle_snapshot=null(), vehicle_snapshot_frozen_at=None, discount_type=None,
            discount_value=None, total_before_discount=None, cost_basis=None,
        )
    )
    db_session.execute(delete(SalesLineItem).where(SalesLineItem.contract_id == contract.id))
    db_session.commit()


@_postgres_only_migration
def test_the_migration_backfills_a_pending_contract_whose_offer_still_matches(db_session, engine):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    _as_before_the_migration(db_session, contract)

    with engine.begin() as conn:
        assert _load_migration().backfill(conn) == (1, 0)
        assert _load_migration().backfill(conn) == (0, 0)  # re-runnable

    db_session.expire_all()
    contract = db_session.get(SalesContract, contract.id)
    assert contract.vehicle_snapshot == offer.vehicle_snapshot
    assert contract.cost_basis == offer.cost_basis
    assert [li.code for li in list_line_items(db_session, deal=contract)] == ["LED"]
    # An edit now rebuilds the contract's own price, not a different one.
    gross = contract.gross_price
    contract = _edit(db_session, contract, vehicle_label=contract.vehicle_label)
    assert contract.gross_price == gross


@_postgres_only_migration
def test_the_migration_skips_a_contract_whose_offer_changed_and_every_signed_one(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    drifted_item = _stock_item(db_session, dealership.id)
    drifted_offer = _offer_with_vehicle(db_session, dealership.id, drifted_item, group_id=group_id)
    drifted = create_contract(db_session, tenant_id=dealership.id, offer=drifted_offer, actor_id=uuid.uuid4())
    _as_before_the_migration(db_session, drifted)
    update_offer(  # the offer was edited after its contract was created
        db_session, offer=drifted_offer, group_id=group_id,
        data=OfferUpdate(discount_type="amount", discount_value=Decimal("100.00")), actor_id=uuid.uuid4(),
    )

    signed_item = _stock_item(db_session, dealership.id, condition=StockItemCondition.NEW)
    signed_offer = _offer_with_vehicle(db_session, dealership.id, signed_item)
    signed = create_contract(db_session, tenant_id=dealership.id, offer=signed_offer, actor_id=uuid.uuid4())
    _as_before_the_migration(db_session, signed)
    signed.status = ContractStatus.CONFIRMED
    db_session.commit()

    with engine.begin() as conn:
        assert _load_migration().backfill(conn) == (0, 1)

    db_session.expire_all()
    assert db_session.get(SalesContract, drifted.id).vehicle_snapshot is None
    assert db_session.get(SalesContract, signed.id).vehicle_snapshot is None
    assert list_line_items(db_session, deal=db_session.get(SalesContract, signed.id)) == []


def test_a_contract_priced_without_inputs_is_not_editable_but_still_confirms(db_session, engine):
    """Review finding: a pending offer-born contract the backfill skipped has
    figures but no snapshot or lines; rebuilding would start from nothing
    (38'800 -> 150). It is refused by reason, and it can still be signed
    as it stands."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item, group_id=group_id)
    offer.customer_id = _customer(db_session, group_id).id
    db_session.commit()
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    _as_before_the_migration(db_session, contract)
    db_session.expire_all()
    contract = db_session.get(SalesContract, contract.id)
    assert {c.id: c.status for c in compute_containers(contract)}["pricing"] == "complete"

    for edit in (
        lambda: _edit(db_session, contract, discount_type="amount", discount_value=Decimal("100.00")),
        lambda: replace_line_items(
            db_session, deal=contract,
            data=LineItemsReplaceRequest(
                accessories=[LineItemAccessoryInput(code="MATS", label="Fussmatten", unit_price=Decimal("150.00"))]
            ),
            actor_id=uuid.uuid4(),
        ),
    ):
        with pytest.raises(ConflictError) as refused:
            edit()
        assert refused.value.details["reason"] == "contract_priced_without_inputs"

    db_session.refresh(contract)
    assert contract.gross_price == Decimal("38800.00")
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    assert contract.status == ContractStatus.CONFIRMED


@_postgres_only_migration
def test_an_edit_waits_for_a_confirmation_in_progress_and_is_then_refused(db_session, engine):
    """Review finding: an edit that read the contract while pending could
    write after it was signed and rewrite the frozen price. Every changing
    route loads the contract `for_update`, so the edit's read waits until
    the confirmation commits — and then sees it signed."""

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from app.sales.services.contract import get_contract_or_404

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))

    confirming = Session(bind=engine)  # the confirm request, holding the row
    editing = Session(bind=engine)  # a PATCH arriving meanwhile
    try:
        held = get_contract_or_404(confirming, dealership.id, contract.id, for_update=True)
        editing.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            get_contract_or_404(editing, dealership.id, contract.id, for_update=True)
        editing.rollback()

        confirm_contract(
            confirming, contract=held, group_id=group_id, actor_id=uuid.uuid4(),
            session_factory=_session_factory(engine),
        )

        seen = get_contract_or_404(editing, dealership.id, contract.id, for_update=True)
        assert seen.status == ContractStatus.CONFIRMED
        with pytest.raises(ConflictError) as refused:
            update_contract(
                editing, contract=seen, data=ContractUpdate(discount_type="amount", discount_value=Decimal("5000.00")),
                actor_id=uuid.uuid4(),
            )
        assert refused.value.details["reason"] == "contract_not_pending"
        editing.rollback()
    finally:
        confirming.close()
        editing.close()

    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).gross_price == Decimal("18000.00")


def test_the_trade_in_routes_refuse_a_contract_priced_without_inputs(db_session):
    """Review hygiene finding: both trade-in routes go through the same guard."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    item = _stock_item(db_session, dealership.id)
    offer = _offer_with_vehicle(db_session, dealership.id, item, group_id=group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    _as_before_the_migration(db_session, contract)
    db_session.expire_all()
    contract = db_session.get(SalesContract, contract.id)
    valuation = _valuation(db_session, dealership.id, group_id, "WVWZZZ1KZAW444444", Decimal("5000.00"))

    for edit in (
        lambda: set_trade_in(
            db_session, deal=contract, group_id=group_id, vin="WVWZZZ1KZAW444444", plate=None, canton=None,
            vehicle_label="Opel Corsa", customer_id=None, actor_id=uuid.uuid4(),
        ),
        lambda: attach_trade_in_valuation(db_session, deal=contract, valuation_id=valuation.id, actor_id=uuid.uuid4()),
    ):
        with pytest.raises(ConflictError) as refused:
            edit()
        assert refused.value.details["reason"] == "contract_priced_without_inputs"


@_postgres_only_migration
@pytest.mark.parametrize("route", ["patch", "line_items", "trade_in"])
def test_an_edit_route_waits_for_a_confirmation_in_progress_and_is_refused(client, db_session, engine, route):
    """KAN-288 review: the lock must be taken by the routes themselves. A
    session holds the contract row as a confirmation does while the real
    route runs in a thread; the route is still blocked after 0.8 s, and once
    the confirmation commits it is refused — as a stale version, because its
    If-Match was read before the signature — and the signed price stands.
    Without `for_update` on the route the edit returns 200 and re-prices the
    signed contract."""

    import threading
    import time

    from sqlalchemy.orm import Session

    from app.sales.services.contract import get_contract_or_404

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _manual_car(db_session, _customer_born_contract(db_session, dealership.id, group_id))
    headers = {**_token(dealership.id, group_id), "If-Match": str(contract.version)}
    requests = {
        "patch": lambda: client.patch(
            f"/v1/sales/contracts/{contract.id}", json={"discountType": "amount", "discountValue": "5000.00"},
            headers=headers,
        ),
        "line_items": lambda: client.put(
            f"/v1/sales/contracts/{contract.id}/line-items",
            json={"accessories": [{"code": "MATS", "label": "Fussmatten", "unitPrice": "150.00"}]},
            headers=headers,
        ),
        "trade_in": lambda: client.post(
            f"/v1/sales/contracts/{contract.id}/trade-in",
            json={"vin": "WVWZZZ1KZAW555555", "vehicleLabel": "Opel Corsa"}, headers=headers,
        ),
    }
    result = {}

    confirming = Session(bind=engine)
    try:
        held = get_contract_or_404(confirming, dealership.id, contract.id, for_update=True)
        sender = threading.Thread(target=lambda: result.update(response=requests[route]()))
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        confirm_contract(
            confirming, contract=held, group_id=group_id, actor_id=uuid.uuid4(),
            session_factory=_session_factory(engine),
        )
        sender.join(10)
    finally:
        confirming.close()

    assert blocked_while_held
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["currentVersion"] == contract.version + 1
    db_session.expire_all()
    signed = db_session.get(SalesContract, contract.id)
    assert (signed.status, signed.gross_price) == (ContractStatus.CONFIRMED, Decimal("18000.00"))
