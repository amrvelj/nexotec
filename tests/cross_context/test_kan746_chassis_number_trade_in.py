"""KAN-746 (Anto, 2026-11-19, option A): a trade-in may take a pre-1981 car's
chassis number, as Stock's purchase booking does since KAN-535 (ADR-167).

The configuration is the one carrier of that number (architect's decision on
this ticket, ADR-167 addendum): it holds the make and the first registration
the booking needs to find or create the car's vehicle-mdm record. So a
chassis-number trade-in is accepted only where the deal's trade-in
configuration carries the number — through a valuation made from such a
configuration — and refused otherwise with 422
`trade_in_chassis_number_requires_configuration`, before anything is written:
without it the deal would take a car Stock then cannot buy. A chassis number
never finds a vehicle-mdm record by itself (KAN-353, ADR-070).

Flows across Sales, Valuation, Stock and Vehicle, so it lives in
cross_context/. On Postgres; the purchase booking over HTTP.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.inventory.models.stock_item import StockItem
from app.sales.models.offer import SalesOffer
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer
from app.sales.services.priced_deal import has_trade_in
from app.sales.services.trade_in import attach_trade_in_valuation, set_trade_in
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
from tests.cross_context.test_configurator_hosts import (
    _record_configuration,
    _session_factory,
    _stock_consumes_the_confirmation,
)
from tests.sales.test_sales_contract_edit import _edit
from tests.sales.test_sales_lifecycle_reservation import _dealership, _with_buyer

CHASSIS = "1130101234567"  # a W113 «Pagode»: 13 digits, no ISO VIN
PRE_1981 = dt.date(1968, 5, 2)
VIN = "WVWZZZ1KZAW746001"


def _oldtimer_configuration(db_session, tenant_id, *, vin: str = CHASSIS) -> VehicleConfiguration:
    """A record-mode configuration carrying the chassis number, the make and
    a first registration before 1981 (the configurator's own KAN-353 rule is
    not this test's subject)."""

    configuration = _record_configuration(db_session, tenant_id)
    configuration.vin = vin
    configuration.brand_display_name = "Mercedes-Benz"
    configuration.model_group_name = "280 SL"
    configuration.first_registration_date = PRE_1981
    db_session.commit()
    return configuration


def _valuation(db_session, tenant_id, group_id, *, configuration_id=None, vin: str | None = None):
    return create_valuation(
        db_session, tenant_id=tenant_id, group_id=group_id,
        data=ValuationCreate(
            configuration_id=configuration_id, vin=vin,
            vehicle_make=None if configuration_id else "Mercedes-Benz",
            vehicle_model=None if configuration_id else "280 SL",
            vehicle_first_registration=None if configuration_id else PRE_1981,
            source="manual", final_offer=Decimal("65000.00"),
        ),
        actor_id=uuid.uuid4(),
    )


def _set(db_session, deal, group_id, vin: str):
    return set_trade_in(
        db_session, deal=deal, group_id=group_id, vin=vin, plate=None, canton=None,
        vehicle_label="Mercedes-Benz 280 SL", customer_id=None, actor_id=uuid.uuid4(),
    )


def _untouched(db_session, offer: SalesOffer, version: int) -> None:
    db_session.rollback()
    db_session.expire_all()
    stored = db_session.get(SalesOffer, offer.id)
    assert (stored.trade_in_vin, stored.trade_in_valuation_id, stored.trade_in_configuration_id) == (None, None, None)
    assert stored.trade_in_value is None and stored.version == version


# --- attaching a valuation ------------------------------------------------------


def test_a_valuation_made_from_an_oldtimers_configuration_is_taken_as_a_trade_in(db_session):
    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id)
    valuation = _valuation(db_session, dealership.id, group_id, configuration_id=configuration.id)
    assert valuation.vehicle_vin == CHASSIS
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert (offer.trade_in_vin, offer.trade_in_vehicle_id) == (CHASSIS, None)
    assert offer.trade_in_configuration_id == configuration.id
    assert offer.trade_in_value == Decimal("65000.00")
    assert has_trade_in(offer)


def test_a_chassis_number_valuation_without_a_configuration_is_refused_and_says_why(db_session):
    """No configuration, no make and first registration for Stock to buy the
    car under: refused at attach, nothing written, with its own reason so the
    screen can say «Konfiguration erfassen»."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    valuation = _valuation(db_session, dealership.id, group_id, vin=CHASSIS)
    assert valuation.configuration_id is None
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    version = offer.version

    with pytest.raises(UnprocessableEntityError) as refused:
        attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert refused.value.details == {
        "reason": "trade_in_chassis_number_requires_configuration", "valuationId": str(valuation.id),
    }
    _untouched(db_session, offer, version)


def test_a_configuration_edited_away_from_its_valuations_number_is_refused(db_session):
    """The configuration must carry the number the valuation names: one
    edited to another car since would let the deal take a car its
    configuration does not describe."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id)
    valuation = _valuation(db_session, dealership.id, group_id, configuration_id=configuration.id)
    configuration.vin = "1130109999999"
    db_session.commit()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    with pytest.raises(UnprocessableEntityError) as refused:
        attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert refused.value.details["reason"] == "trade_in_chassis_number_requires_configuration"


# --- typing the number (set_trade_in) ------------------------------------------


def test_a_typed_chassis_number_takes_the_valuation_of_its_configuration(db_session):
    """FR-S-08: the valid valuation of that number is offered (auto-attached),
    and it brings the configuration. Typed in another spelling, it is the
    same number; it never resolves vehicle-mdm by itself."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id)
    valuation = _valuation(db_session, dealership.id, group_id, configuration_id=configuration.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = _set(db_session, offer, group_id, " 1130 1012 34567 ")

    assert (offer.trade_in_vin, offer.trade_in_vehicle_id) == (CHASSIS, None)
    assert offer.trade_in_valuation_id == valuation.id
    assert offer.trade_in_configuration_id == configuration.id


def test_a_typed_chassis_number_without_a_configuration_is_refused(db_session):
    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    version = offer.version

    with pytest.raises(UnprocessableEntityError) as refused:
        _set(db_session, offer, group_id, CHASSIS)

    assert refused.value.details == {"field": "vin", "reason": "trade_in_chassis_number_requires_configuration"}
    _untouched(db_session, offer, version)


def test_a_chassis_number_whose_only_valuation_has_no_configuration_is_refused(db_session):
    """Such a valuation is not offered (it could not be attached), so the deal
    still has no configuration carrying the number."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    _valuation(db_session, dealership.id, group_id, vin=CHASSIS)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    version = offer.version

    with pytest.raises(UnprocessableEntityError) as refused:
        _set(db_session, offer, group_id, CHASSIS)

    assert refused.value.details["reason"] == "trade_in_chassis_number_requires_configuration"
    _untouched(db_session, offer, version)


def test_a_chassis_number_the_deals_own_configuration_carries_is_taken_without_a_valuation(db_session):
    """The deal already carries the oldtimer's configuration (its valuation
    was attached, then expired, say): typing its number finds no valuation to
    offer, and the deal's own configuration carries it — taken."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer.trade_in_configuration_id = configuration.id
    db_session.commit()

    offer = _set(db_session, offer, group_id, CHASSIS)

    assert (offer.trade_in_vin, offer.trade_in_vehicle_id, offer.trade_in_valuation_id) == (CHASSIS, None, None)
    assert offer.trade_in_configuration_id == configuration.id


def test_a_chassis_number_another_cars_configuration_does_not_carry_is_refused(db_session):
    """A deal whose trade-in configuration describes another car (here a VIN
    car) does not take a chassis number typed over it."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id, vin=VIN)
    configuration.first_registration_date = dt.date(2010, 1, 1)
    db_session.commit()
    valuation = _valuation(db_session, dealership.id, group_id, configuration_id=configuration.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())
    assert offer.trade_in_configuration_id == configuration.id

    with pytest.raises(UnprocessableEntityError) as refused:
        _set(db_session, offer, group_id, CHASSIS)

    assert refused.value.details["reason"] == "trade_in_chassis_number_requires_configuration"


# --- over HTTP: the request takes the chassis number ---------------------------


def _sales_headers(dealership, version: int) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}", "If-Match": str(version)}


def test_the_trade_in_route_takes_a_chassis_number_and_refuses_one_with_punctuation(client, db_session):
    dealership = _dealership(db_session)
    configuration = _oldtimer_configuration(db_session, dealership.id)
    _valuation(db_session, dealership.id, dealership.dealer_group_id, configuration_id=configuration.id)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    path = f"/v1/sales/offers/{offer.id}/trade-in"

    punctuated = client.post(
        path, json={"vin": "113010-1234567", "vehicleLabel": "Mercedes-Benz 280 SL"},
        headers=_sales_headers(dealership, offer.version),
    )
    assert punctuated.status_code == 422, punctuated.text
    assert punctuated.json()["error"]["details"]["errors"][0]["loc"][-1] == "vin"

    taken = client.post(
        path, json={"vin": "1130 1012 34567", "vehicleLabel": "Mercedes-Benz 280 SL"},
        headers=_sales_headers(dealership, offer.version),
    )
    assert taken.status_code == 200, taken.text
    assert (taken.json()["tradeInVin"], taken.json()["tradeInConfigurationId"]) == (CHASSIS, str(configuration.id))


def test_the_trade_in_route_answers_the_refusal_with_its_reason(client, db_session):
    dealership = _dealership(db_session)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    refused = client.post(
        f"/v1/sales/offers/{offer.id}/trade-in", json={"vin": CHASSIS, "vehicleLabel": "Mercedes-Benz 280 SL"},
        headers=_sales_headers(dealership, offer.version),
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {
        "field": "vin", "reason": "trade_in_chassis_number_requires_configuration",
    }


# --- the whole way: contract → pipeline → purchase → vehicle-mdm --------------


def test_a_chassis_number_trade_in_is_bought_onto_stock_under_its_number(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    configuration = _oldtimer_configuration(db_session, dealership.id)
    valuation = _valuation(db_session, dealership.id, group_id, configuration_id=configuration.id)
    offer = _with_buyer(db_session, create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()), group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Golf",
        manual_vehicle_condition="used", manual_base_price=Decimal("80000.00"),
    )
    contract = attach_trade_in_valuation(db_session, deal=contract, valuation_id=valuation.id, actor_id=uuid.uuid4())
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    _stock_consumes_the_confirmation(db_session, contract)

    db_session.expire_all()
    item = db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")
    ).one()
    # Stock keeps the number the contract holds, and the configuration carrying it.
    assert (item.trade_in_vin, item.vin, item.configuration_id) == (CHASSIS, None, configuration.id)
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 0

    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=group_id, roles=frozenset({AccessRole.INVENTORY})
    )
    bought = client.post(
        f"/v1/inventory/stock-items/{item.id}/purchase",
        json={
            "supplierName": "Hans Muster", "supplierIsVatRegistered": False, "purchasePrice": "60000.00",
            "purchaseDate": "2026-11-20", "vin": CHASSIS,
        },
        headers={"Authorization": f"Bearer {token}", "If-Match": str(item.version)},
    )

    assert bought.status_code == 200, bought.text
    assert (bought.json()["lifecycleStatus"], bought.json()["vin"]) == ("in_stock", CHASSIS)
    db_session.expire_all()
    [record] = db_session.scalars(select(VehicleMdm)).all()
    assert record.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER
    assert (record.vin, record.chassis_make_key, record.first_registration_date) == (CHASSIS, "mercedesbenz", PRE_1981)
