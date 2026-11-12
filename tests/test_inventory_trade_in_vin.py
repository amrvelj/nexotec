"""KAN-536 (Anto, 2026-11-12, option A): «Einkauf buchen» prefills the VIN a
trade-in's contract already holds and refuses another, the way a
configuration's VIN is checked (KAN-510).

- `sales.contract.confirmed`'s `tradeIn` block carries the contract's VIN
  (additive `vin`; left out when the contract holds none);
- Stock's consumer keeps it on the trade-in's pipeline item as
  `trade_in_vin` — never as `vin`, never a vehicle-mdm record (ADR-045) —
  once, idempotently, and tolerates a confirmation without it;
- the item's read exposes it (`tradeInVin`) to the item's own Betrieb only;
- the booking refuses another VIN (422 `trade_in_vin_differs`, nothing
  written), books the contract's in any spelling, lets a configuration's VIN
  decide when there is one, and keeps what the contract said through a
  storno and the second booking.

Postgres lane; the booking and the read over HTTP.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.inventory.consumers import handle_sales_contract_confirmed_message
from app.inventory.models.stock_item import LifecycleStatus, StockItem
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer
from app.sales.services.trade_in import set_trade_in
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.test_configurator_hosts import (
    _message,
    _record_configuration,
    _session_factory,
    _stock_consumes_the_confirmation,
)
from tests.test_sales_contract_edit import _edit
from tests.test_sales_lifecycle_reservation import _dealership, _with_buyer

CONTRACT_VIN = "WVWZZZ1KZAW536001"
OTHER_VIN = "WVWZZZ1KZAW536002"
CONFIGURED_VIN = "WVWZZZ1KZAW536003"

_PURCHASE = {
    "supplierName": "Hans Muster",
    "supplierIsVatRegistered": False,
    "purchasePrice": "8000.00",
    "purchaseDate": "2026-11-10",
}


def _confirmed_trade_in_contract(db_session, engine, dealership=None):
    """A signed contract whose trade-in was recorded by its VIN alone."""

    dealership = dealership or _dealership(db_session)
    group_id = dealership.dealer_group_id
    offer = _with_buyer(db_session, create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()), group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = set_trade_in(
        db_session, deal=contract, group_id=group_id, vin=CONTRACT_VIN, plate=None, canton=None,
        vehicle_label="Seat Leon", customer_id=None, actor_id=uuid.uuid4(),
    )
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    return dealership, contract


def _trade_in_item(db_session, contract) -> StockItem:
    db_session.expire_all()
    return db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")
    ).one()


def _pipeline_trade_in(db_session, engine, *, event_vin: object = CONTRACT_VIN):
    """The trade-in's pipeline item as Stock's consumer builds it. `event_vin`
    rewrites what the confirmation says: `None` is a confirmation published
    before KAN-536 (no `vin` key at all)."""

    dealership, contract = _confirmed_trade_in_contract(db_session, engine)
    if event_vin != CONTRACT_VIN:
        confirmed = _message(db_session, "sales.contract.confirmed", contract.id)
        trade_in = {k: v for k, v in confirmed.payload["tradeIn"].items() if k != "vin"}
        if event_vin is not None:
            trade_in["vin"] = event_vin
        confirmed.payload = {**confirmed.payload, "tradeIn": trade_in}
        db_session.commit()
    _stock_consumes_the_confirmation(db_session, contract)
    return dealership, contract, _trade_in_item(db_session, contract)


def _auth(tenant_id: uuid.UUID, group_id: uuid.UUID, **extra: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=group_id, roles=frozenset({AccessRole.INVENTORY})
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _purchase(client, dealership, item: StockItem, **extra: str):
    return client.post(
        f"/v1/inventory/stock-items/{item.id}/purchase",
        json={**_PURCHASE, **extra},
        headers=_auth(dealership.id, dealership.dealer_group_id, **{"If-Match": str(item.version)}),
    )


def _vehicle_mdm_rows(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(VehicleMdm))


def _events(db_session, item_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OutboxMessage.event_type).where(OutboxMessage.aggregate_id == item_id).order_by(OutboxMessage.created_at)
        )
    )


# --- The event ------------------------------------------------------------------


def test_the_confirmation_names_the_trade_ins_vin(db_session, engine):
    _dealership_row, contract = _confirmed_trade_in_contract(db_session, engine)
    assert contract.trade_in_vin == CONTRACT_VIN

    payload = _message(db_session, "sales.contract.confirmed", contract.id).payload

    assert payload["tradeIn"] == {"vehicleLabel": "Seat Leon", "condition": "used", "vin": CONTRACT_VIN}


# --- The consumer ---------------------------------------------------------------


def test_the_pipeline_item_keeps_the_vin_as_known_future_vin_and_has_no_vin_of_its_own(db_session, engine):
    _dealership_row, _contract, item = _pipeline_trade_in(db_session, engine)

    assert item.trade_in_vin == CONTRACT_VIN
    # ADR-045: still a pipeline item without a VIN and without a vehicle-mdm record.
    assert (item.lifecycle_status, item.vin, item.vehicle_id) == (LifecycleStatus.PIPELINE, None, None)
    assert _vehicle_mdm_rows(db_session) == 0


def test_a_redelivered_confirmation_creates_no_second_item_and_keeps_the_vin(db_session, engine):
    _dealership_row, contract, item = _pipeline_trade_in(db_session, engine)
    message = _message(db_session, "sales.contract.confirmed", contract.id)

    # The same message again (at-least-once): the processed-events table stops it.
    handled = consume_once(
        db_session, message=message, consumer_name="inventory.sales_contract_confirmed",
        handler=handle_sales_contract_confirmed_message,
    )
    assert handled is False
    # A duplicate emission under another consumer name reaches the handler:
    # the item is found by its pipeline_ref and left as it is.
    message.payload = {**message.payload, "tradeIn": {**message.payload["tradeIn"], "vin": OTHER_VIN}}
    db_session.commit()
    consume_once(
        db_session, message=message, consumer_name="test.kan536.duplicate_emission",
        handler=handle_sales_contract_confirmed_message,
    )

    again = _trade_in_item(db_session, contract)  # `.one()`: no second item
    assert again.id == item.id
    assert again.trade_in_vin == CONTRACT_VIN
    assert again.version == item.version


def test_a_confirmation_without_the_vin_leaves_the_item_without_and_it_is_bought_as_before(client, db_session, engine):
    """A confirmation already in the outbox when KAN-536 deploys, and every
    trade-in item created before it: nothing to prefill, nothing to check."""

    dealership, _contract, item = _pipeline_trade_in(db_session, engine, event_vin=None)
    assert item.trade_in_vin is None

    response = _purchase(client, dealership, item, vin=OTHER_VIN)

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == OTHER_VIN
    assert response.json()["tradeInVin"] is None


def test_a_pre_1981_chassis_number_is_not_kept(client, db_session, engine):
    """A contract can hold a chassis number through a configured valuation
    (KAN-353). It is no VIN a purchase can be booked with, so Stock keeps
    none and the booking is exactly as before KAN-536 (KAN-535 stays open)."""

    dealership, _contract, item = _pipeline_trade_in(db_session, engine, event_vin="1303 2445")
    assert item.trade_in_vin is None

    assert _purchase(client, dealership, item, vin=OTHER_VIN).status_code == 200


def test_a_vin_in_another_spelling_is_kept_in_the_one_spelling(db_session, engine):
    _dealership_row, _contract, item = _pipeline_trade_in(db_session, engine, event_vin=" wvwzzz1kzaw 536001 ")

    assert item.trade_in_vin == CONTRACT_VIN


def test_a_value_that_is_no_text_is_ignored_not_dead_lettered(db_session, engine):
    _dealership_row, _contract, item = _pipeline_trade_in(db_session, engine, event_vin=536)

    assert item.trade_in_vin is None


# --- The read that feeds the dialog -----------------------------------------------


def test_the_items_read_exposes_the_vin_to_its_own_betrieb_only(client, db_session, engine):
    dealership, _contract, item = _pipeline_trade_in(db_session, engine)

    own = client.get(f"/v1/inventory/stock-items/{item.id}", headers=_auth(dealership.id, dealership.dealer_group_id))
    assert own.status_code == 200, own.text
    assert own.json()["tradeInVin"] == CONTRACT_VIN
    assert own.json()["vin"] is None

    other = _dealership(db_session)
    foreign = client.get(f"/v1/inventory/stock-items/{item.id}", headers=_auth(other.id, other.dealer_group_id))
    assert foreign.status_code == 404  # never a 403 (rule 7)
    assert CONTRACT_VIN not in foreign.text


def test_the_group_wide_stock_read_does_not_carry_it(client, db_session, engine):
    """ADR-055: the group projection is enumerated; the contract's VIN is the
    Betrieb's own."""

    from app.inventory.schemas.group_listing import StockItemGroupRead

    assert "trade_in_vin" not in StockItemGroupRead.model_fields


# --- The booking ----------------------------------------------------------------


def test_another_vin_is_refused_and_nothing_is_written(client, db_session, engine):
    dealership, _contract, item = _pipeline_trade_in(db_session, engine)
    version, events_before = item.version, _events(db_session, item.id)

    response = _purchase(client, dealership, item, vin=OTHER_VIN)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "vin", "reason": "trade_in_vin_differs"}
    stored = _trade_in_item(db_session, _contract)
    assert (stored.lifecycle_status, stored.vin, stored.vehicle_id) == (LifecycleStatus.PIPELINE, None, None)
    assert (stored.purchase_price, stored.supplier_name, stored.version) == (None, None, version)
    assert stored.trade_in_vin == CONTRACT_VIN
    assert _events(db_session, item.id) == events_before
    assert _vehicle_mdm_rows(db_session) == 0  # refused before vehicle-mdm is asked for the other car


def test_the_contracts_vin_books_the_purchase_in_any_spelling(client, db_session, engine):
    dealership, _contract, item = _pipeline_trade_in(db_session, engine)

    response = _purchase(client, dealership, item, vin=" wvwzzz1kzaw 536001 ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["lifecycleStatus"], body["vin"], body["isInvoiceable"]) == ("in_stock", CONTRACT_VIN, True)
    assert body["tradeInVin"] == CONTRACT_VIN  # what the contract said stays, as history


def test_the_booking_still_needs_the_vin_sent(client, db_session, engine):
    """As for a configuration's VIN (KAN-510): the dialog prefills, the API
    takes no VIN it was not given."""

    dealership, _contract, item = _pipeline_trade_in(db_session, engine)

    response = _purchase(client, dealership, item)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "purchase_requires_vin"


def test_a_configurations_vin_decides_alone(client, db_session, engine):
    """A trade-in with a configuration carrying a VIN: the configuration is
    the car's technical data and where a wrong VIN is corrected, so it wins
    (KAN-510, KAN-575) — the two never have to agree, or a corrected
    configuration would leave the car unbuyable."""

    dealership, _contract, item = _pipeline_trade_in(db_session, engine)
    item.configuration_id = _record_configuration(db_session, dealership.id, vin=CONFIGURED_VIN).id
    db_session.commit()

    refused = _purchase(client, dealership, item, vin=CONTRACT_VIN)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"field": "vin", "reason": "configuration_vin_differs"}

    booked = _purchase(client, dealership, item, vin=CONFIGURED_VIN)
    assert booked.status_code == 200, booked.text
    assert booked.json()["vin"] == CONFIGURED_VIN


def test_a_storno_keeps_what_the_contract_said_and_the_car_is_booked_again_under_its_vin(client, db_session, engine):
    dealership, contract, item = _pipeline_trade_in(db_session, engine)
    bought = _purchase(client, dealership, item, vin=CONTRACT_VIN).json()

    stornoed = client.post(
        f"/v1/inventory/stock-items/{item.id}/purchase/storno",
        json={"reason": "Falsch gebucht"},
        headers=_auth(dealership.id, dealership.dealer_group_id, **{"If-Match": str(bought["version"])}),
    )
    assert stornoed.status_code == 200, stornoed.text
    body = stornoed.json()
    # KAN-146: back in pipeline with its VIN and record; the contract's VIN untouched.
    assert (body["lifecycleStatus"], body["vin"], body["tradeInVin"]) == ("pipeline", CONTRACT_VIN, CONTRACT_VIN)
    assert body["vehicleId"] == bought["vehicleId"]

    item = _trade_in_item(db_session, contract)
    # The stornoed car's own VIN binds the second booking (`purchase_vin_mismatch`), as before.
    other = _purchase(client, dealership, item, vin=OTHER_VIN)
    assert other.status_code == 422 and other.json()["error"]["details"]["reason"] == "purchase_vin_mismatch"
    again = _purchase(client, dealership, item)
    assert again.status_code == 200, again.text
    assert (again.json()["lifecycleStatus"], again.json()["vin"]) == ("in_stock", CONTRACT_VIN)
