"""KAN-189 (Anto, 2026-10-31 option B, 2026-11-01 option A; ADR-070 and
FR-S-08 amended): neither a VIN-only valuation nor an offer's VIN trade-in
creates a vehicle-mdm record. The car enters the register when it is bought
(ADR-103). What keeps working without the record:

- FR-S-08's "an existing valid valuation is offered before a new one" — the
  trade-in finds the valuation by its VIN (`list_valid_valuations_for_vin`);
- the trade-in container reads complete;
- confirmation still makes the trade-in a pipeline item, with its valuation
  pointer (ADR-074), and still writes no vehicle-mdm record.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.inventory.models.stock_item import LifecycleStatus, StockItem
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer
from app.sales.services.priced_deal import compute_containers
from app.sales.services.trade_in import set_trade_in
from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation, list_valid_valuations_for_vin
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.test_configurator_hosts import _message, _session_factory, _stock_consumes_the_confirmation
from tests.test_sales_contract_edit import _edit
from tests.test_sales_lifecycle_reservation import _dealership, _with_buyer


def _vehicle_mdm_rows(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(VehicleMdm))


def test_a_vin_only_valuation_is_found_by_its_vin_normalised(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WVWZZZ1KZAW189001", source=ValuationSource.MANUAL, final_offer=Decimal(9000)),
        actor_id=uuid.uuid4(),
    )

    assert [v.id for v in list_valid_valuations_for_vin(db_session, tenant_id=tenant_id, vin="wvwzzz1kzaw 189001")] == [
        valuation.id
    ]
    # Tenant-private (ADR-029): another Betrieb never finds it.
    assert list_valid_valuations_for_vin(db_session, tenant_id=uuid.uuid4(), vin="WVWZZZ1KZAW189001") == []


def test_a_row_stored_before_the_create_normalised_its_vin_is_found_too(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WVWZZZ1KZAW189002", source=ValuationSource.MANUAL, final_offer=Decimal(9000)),
        actor_id=uuid.uuid4(),
    )
    db_session.execute(
        Valuation.__table__.update().where(Valuation.id == valuation.id).values(vehicle_vin="wvwzzz1kzaw189002")
    )
    db_session.commit()

    assert [v.id for v in list_valid_valuations_for_vin(db_session, tenant_id=tenant_id, vin="WVWZZZ1KZAW189002")] == [
        valuation.id
    ]


def test_a_draft_or_expired_valuation_is_not_offered_by_vin(db_session):
    tenant_id = uuid.uuid4()
    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(
            vin="WVWZZZ1KZAW189003", source=ValuationSource.MANUAL, final_offer=Decimal(9000), is_draft=True
        ),
        actor_id=uuid.uuid4(),
    )

    assert list_valid_valuations_for_vin(db_session, tenant_id=tenant_id, vin="WVWZZZ1KZAW189003") == []


def test_the_vin_only_valuation_is_auto_attached_and_the_container_reads_complete(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WVWZZZ1KZAW189004", source=ValuationSource.MANUAL, final_offer=Decimal(11000)),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    updated = set_trade_in(
        db_session, deal=offer, group_id=uuid.uuid4(), vin="WVWZZZ1KZAW189004", plate=None, canton=None,
        vehicle_label="Seat Leon", customer_id=None, actor_id=uuid.uuid4(),
    )

    assert updated.trade_in_vehicle_id is None
    assert updated.trade_in_valuation_id == valuation.id
    assert updated.trade_in_value == Decimal(11000)
    trade_in_container = next(c for c in compute_containers(updated) if c.id == "trade_in")
    assert trade_in_container.status == "complete"
    assert _vehicle_mdm_rows(db_session) == 0


def test_confirmation_makes_a_vin_only_trade_in_a_pipeline_item_with_its_valuation(db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=dealership.id, group_id=group_id,
        data=ValuationCreate(vin="WVWZZZ1KZAW189005", source=ValuationSource.MANUAL, final_offer=Decimal(7000)),
        actor_id=uuid.uuid4(),
    )
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = _with_buyer(db_session, offer, group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = set_trade_in(
        db_session, deal=contract, group_id=group_id, vin="WVWZZZ1KZAW189005", plate=None, canton=None,
        vehicle_label="Seat Leon", customer_id=None, actor_id=uuid.uuid4(),
    )
    assert (contract.trade_in_vehicle_id, contract.trade_in_valuation_id) == (None, valuation.id)

    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )

    payload = _message(db_session, "sales.contract.confirmed", contract.id).payload
    assert payload["tradeIn"] == {"vehicleLabel": "Seat Leon", "condition": "used", "valuationId": str(valuation.id)}

    _stock_consumes_the_confirmation(db_session, contract)
    trade_in_item = db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")
    ).one()
    assert trade_in_item.lifecycle_status == LifecycleStatus.PIPELINE
    assert trade_in_item.valuation_ref_id == valuation.id
    assert trade_in_item.vehicle_id is None
    assert _vehicle_mdm_rows(db_session) == 0
