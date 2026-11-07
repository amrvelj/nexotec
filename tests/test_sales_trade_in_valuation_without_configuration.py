"""KAN-569: a valuation made without a configuration, attached to a deal
that has no trade-in car yet («Offerte aus dieser Bewertung erstellen»),
brings its car with it.

Before, `_attach` copied a car only from the valuation's configuration: such
a valuation gave the offer its value (the payable dropped by it) but no VIN
and no label, so `priced_deal.has_trade_in` was false, the workspace's
trade-in container said nothing, and confirming the contract took no car
into the pipeline. Now the valuation's stored VIN, the record it linked
(KAN-189) and its «Make Model Trim» become the trade-in, as a VIN trade-in
(`set_trade_in`) makes them. On Postgres.
"""

import uuid
from decimal import Decimal

from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.priced_deal import has_trade_in
from app.sales.services.trade_in import attach_trade_in_valuation, set_trade_in
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from tests.stock_support import create_in_stock_item
from tests.test_sales_lifecycle_reservation import _customer, _dealership
from tests.test_sales_trade_in_valuation_use import _confirm

VIN = "WVWZZZ1KZAW569001"


def _valuation(db_session, tenant_id, group_id, *, vin: str | None = VIN, named: bool = True):
    return create_valuation(
        db_session, tenant_id=tenant_id, group_id=group_id,
        data=ValuationCreate(
            vin=vin,
            vehicle_make="VW" if named else None,
            vehicle_model="Golf" if named else None,
            vehicle_trim="1.5 TSI Life" if named else None,
            source="manual",
            final_offer=Decimal("12000.00"),
        ),
        actor_id=uuid.uuid4(),
    )


def test_a_valuation_without_a_configuration_brings_its_car_to_a_deal_without_one(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    assert valuation.configuration_id is None
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert offer.trade_in_valuation_id == valuation.id
    assert offer.trade_in_vin == VIN
    assert offer.trade_in_vehicle_id is None  # no record in the register for this VIN
    assert offer.trade_in_label == "VW Golf 1.5 TSI Life"
    assert offer.trade_in_value == Decimal("12000.00")
    assert has_trade_in(offer)


def test_a_deal_that_already_names_its_trade_in_car_keeps_it(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = set_trade_in(
        db_session, deal=offer, group_id=group_id, vin="TMBJJ7NE0L0569002", plate=None, canton=None,
        vehicle_label="Skoda Octavia Combi", customer_id=None, actor_id=uuid.uuid4(),
    )
    valuation = _valuation(db_session, dealership.id, group_id)

    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert offer.trade_in_vin == "TMBJJ7NE0L0569002"
    assert offer.trade_in_label == "Skoda Octavia Combi"
    assert offer.trade_in_value == Decimal("12000.00")


def test_a_valuation_without_a_vin_brings_no_car(db_session):
    """Unchanged: a valuation naming no car gives the value only (KAN-569
    follow-up ticket asks Anto whether such an attach should be refused)."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id, vin=None)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert offer.trade_in_vin is None
    assert offer.trade_in_label is None
    assert not has_trade_in(offer)


def test_its_contract_takes_the_car_into_the_pipeline_on_confirmation(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id)
    item = create_in_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Skoda Octavia 2.0 TDI", condition=StockItemCondition.USED, vin="TMBJJ7NE0L0569003"),
    )
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(
            customer_id=_customer(db_session, group_id).id, vehicle_source="stock", stock_item_id=item.id,
            vehicle_label=item.vehicle_label,
        ),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())

    _confirm(db_session, engine, contract, group_id)

    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    assert message.payload["tradeIn"]["vehicleLabel"] == "VW Golf 1.5 TSI Life"
    assert message.payload["tradeIn"]["valuationId"] == str(valuation.id)


def test_a_valuation_with_a_vin_but_no_make_or_model_is_named_by_its_vin(db_session, engine):
    """The label is never empty: the confirmation publishes it and Stock's
    consumer names the pipeline item by it (`StockItemCreate.vehicle_label`
    is required) — an empty one would dead-letter the confirmation."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id, named=False)
    item = create_in_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Skoda Octavia 2.0 TDI", condition=StockItemCondition.USED, vin="TMBJJ7NE0L0569004"),
    )
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())
    assert offer.trade_in_label == VIN
    offer = update_offer(
        db_session, offer=offer, group_id=group_id,
        data=OfferUpdate(
            customer_id=_customer(db_session, group_id).id, vehicle_source="stock", stock_item_id=item.id,
            vehicle_label=item.vehicle_label,
        ),
        actor_id=uuid.uuid4(),
    )
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())

    _confirm(db_session, engine, contract, group_id)

    message = db_session.query(OutboxMessage).filter_by(
        aggregate_id=contract.id, event_type="sales.contract.confirmed"
    ).one()
    assert message.payload["tradeIn"]["vehicleLabel"] == VIN


def test_a_chassis_number_stored_on_the_valuation_brings_no_car(db_session):
    """A trade-in takes only a VIN (`NormalisedVin`); a pre-1981 chassis
    number a valuation stored (KAN-353) brings its value only, as before."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = _valuation(db_session, dealership.id, group_id, vin=None)
    valuation.vehicle_vin = "1302412345"  # as a chassis number is stored; ValuationCreate's own rule aside
    db_session.commit()
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    offer = attach_trade_in_valuation(db_session, deal=offer, valuation_id=valuation.id, actor_id=uuid.uuid4())

    assert offer.trade_in_vin is None
    assert not has_trade_in(offer)
