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

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.customer.consumers import handle_stock_item_purchased_message as handle_trade_in_purchased_message
from app.customer.consumers import purchase_moment
from app.customer.models.customer import Customer, CustomerLifecycleStatus
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.customer import allocate_vehicle_party, list_customer_vehicles
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import Dealership
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
from tests.test_sales_lifecycle_reservation import _customer, _dealership, _with_buyer


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


# --- KAN-189 option A (Anto, 2026-11-02): the purchase records the former owner ---

_TRADE_IN_VIN = "WVWZZZ1KZAW189010"


def _bought_trade_in(db_session, engine, *, purchase_date=dt.date(2026, 10, 20)):
    """A contract whose VIN trade-in Stock turned into a pipeline item, then
    bought: returns (contract, item, purchased message)."""

    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    offer = _with_buyer(db_session, create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()), group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = set_trade_in(
        db_session, deal=contract, group_id=group_id, vin=_TRADE_IN_VIN, plate=None, canton=None,
        vehicle_label="Seat Leon", customer_id=None, actor_id=uuid.uuid4(),
    )
    contract = confirm_contract(
        db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(),
        session_factory=_session_factory(engine),
    )
    # Signed weeks before the car comes in, as in a dealer's week.
    contract.signed_at = _SIGNED_AT
    db_session.commit()
    _stock_consumes_the_confirmation(db_session, contract)
    item = db_session.scalars(
        select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")
    ).one()
    assert _parties(db_session) == []  # nothing links the car before it is bought
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="Privat", supplier_is_vat_registered=False, purchase_price=Decimal("9000.00"),
            purchase_date=purchase_date, vin=_TRADE_IN_VIN,
        ),
        actor_id=_BUYER_ACTOR,
    )
    return contract, item, _message(db_session, "inventory.stock_item.purchased", item.id)


_BUYER_ACTOR = uuid.uuid4()
_SIGNED_AT = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)


def _parties(db_session) -> list[VehicleParty]:
    return list(db_session.scalars(select(VehicleParty).order_by(VehicleParty.role)).all())


def _customer_consumes(db_session, message) -> bool:
    return consume_once(
        db_session, message=message, consumer_name="customer.trade_in_purchased",
        handler=handle_trade_in_purchased_message,
    )


def test_the_purchased_event_names_the_vehicle_the_contract_and_the_date(db_session, engine):
    contract, item, message = _bought_trade_in(db_session, engine)

    assert message.payload == {
        "stockNumber": item.stock_number,
        "vehicleId": str(item.vehicle_id),
        "purchaseDate": "2026-10-20",
        "purchasedBy": str(_BUYER_ACTOR),
        "originContractId": str(contract.id),
        "originRole": "trade_in",
    }


def test_buying_the_trade_in_records_its_customer_as_closed_former_owner_and_keeper(db_session, engine):
    contract, item, message = _bought_trade_in(db_session, engine)
    vehicle = db_session.scalars(select(VehicleMdm)).one()
    assert vehicle.vin == _TRADE_IN_VIN and item.vehicle_id == vehicle.id  # the purchase created the record

    assert _customer_consumes(db_session, message) is True

    parties = _parties(db_session)
    assert [(p.role, p.customer_id, p.vehicle_id) for p in parties] == [
        (VehiclePartyRole.KEEPER, contract.customer_id, vehicle.id),
        (VehiclePartyRole.OWNER, contract.customer_id, vehicle.id),
    ]
    # Closed at the start of the purchase date in Switzerland; held since the contract was signed.
    ended = dt.datetime(2026, 10, 19, 22, 0, tzinfo=dt.UTC)
    assert {p.effective_to for p in parties} == {ended}
    assert {p.effective_from for p in parties} == {_SIGNED_AT}
    assert all(p.vehicle_vin == _TRADE_IN_VIN for p in parties)  # rule 2's label written with the row

    # The customer's Vehicles tab (history included) now shows the car; the current view does not.
    assert {p.vehicle_id for p in list_customer_vehicles(db_session, customer_id=contract.customer_id, include_closed=True)} == {vehicle.id}
    assert list_customer_vehicles(db_session, customer_id=contract.customer_id) == []
    linked = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.vehicle_party.linked")
    ).all()
    assert len(linked) == 2


def test_a_purchase_dated_before_the_signature_closes_at_the_booking(db_session, engine):
    contract, _item, message = _bought_trade_in(db_session, engine, purchase_date=dt.date(2020, 1, 1))

    _customer_consumes(db_session, message)

    parties = _parties(db_session)
    assert {p.effective_to for p in parties} == {message.occurred_at}
    assert all(p.effective_from < p.effective_to for p in parties)


def test_a_redelivered_purchase_links_once(db_session, engine):
    _contract, _item, message = _bought_trade_in(db_session, engine)

    assert _customer_consumes(db_session, message) is True
    assert _customer_consumes(db_session, message) is False
    # Even past the harness, the handler itself writes the history once.
    handle_trade_in_purchased_message(db_session, message)
    db_session.commit()

    assert len(_parties(db_session)) == 2


def test_a_customer_merged_since_the_contract_gets_it_on_the_survivor(db_session, engine):
    contract, _item, message = _bought_trade_in(db_session, engine)
    survivor = _customer(db_session, contract_group_id(db_session, contract))
    duplicate = db_session.get(Customer, contract.customer_id)
    duplicate.lifecycle_status = CustomerLifecycleStatus.MERGED
    duplicate.duplicate_of_customer_id = survivor.id
    db_session.commit()

    _customer_consumes(db_session, message)

    assert {p.customer_id for p in _parties(db_session)} == {survivor.id}


def test_an_open_link_the_customer_already_had_is_closed_at_the_purchase(db_session, engine):
    contract, item, message = _bought_trade_in(db_session, engine)
    group_id = contract_group_id(db_session, contract)
    earlier = allocate_vehicle_party(
        db_session, vehicle_id=item.vehicle_id, customer_id=contract.customer_id, role=VehiclePartyRole.OWNER,
        group_id=group_id, actor_id=uuid.uuid4(), effective_from=dt.datetime(2019, 5, 1, tzinfo=dt.UTC),
    )

    _customer_consumes(db_session, message)

    owners = [p for p in _parties(db_session) if p.role == VehiclePartyRole.OWNER]
    assert [p.id for p in owners] == [earlier.id]  # no second row beside it
    assert owners[0].effective_to == dt.datetime(2026, 10, 19, 22, 0, tzinfo=dt.UTC)


def test_another_holder_in_the_group_is_left_alone(db_session, engine):
    contract, item, message = _bought_trade_in(db_session, engine)
    group_id = contract_group_id(db_session, contract)
    other = allocate_vehicle_party(
        db_session, vehicle_id=item.vehicle_id, customer_id=_customer(db_session, group_id).id,
        role=VehiclePartyRole.DRIVER, group_id=group_id, actor_id=uuid.uuid4(),
    )

    _customer_consumes(db_session, message)

    db_session.refresh(other)
    assert other.effective_to is None  # history closes nobody (KAN-148, KAN-370)


def test_a_purchase_that_is_not_a_trade_in_links_nobody(db_session, engine):
    dealership = _dealership(db_session)
    item = create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Seat Ibiza", condition=StockItemCondition.USED), actor_id=uuid.uuid4(),
    )
    record_purchase(
        db_session, item=item,
        data=RecordPurchaseRequest(
            supplier_name="AMAG", supplier_is_vat_registered=True, purchase_price=Decimal("12000.00"),
            purchase_date=dt.date(2026, 10, 1), vin="WVWZZZ6JZ8W189011",
        ),
        actor_id=uuid.uuid4(),
    )
    message = _message(db_session, "inventory.stock_item.purchased", item.id)
    assert "originRole" not in message.payload

    assert _customer_consumes(db_session, message) is True
    assert _parties(db_session) == []


def test_purchase_moment_uses_the_swiss_day():
    known = dt.datetime(2026, 3, 1, 9, 0, tzinfo=dt.UTC)
    booked = dt.datetime(2026, 7, 2, 9, 0, tzinfo=dt.UTC)
    # Summer time: the Swiss day starts at 22:00 UTC the evening before.
    assert purchase_moment("2026-07-01", booked_at=booked, known_since=known) == dt.datetime(
        2026, 6, 30, 22, 0, tzinfo=dt.UTC
    )
    assert purchase_moment(None, booked_at=booked, known_since=known) == booked
    assert purchase_moment("2026-03-01", booked_at=booked, known_since=known) == booked


def contract_group_id(db_session, contract) -> uuid.UUID:
    return db_session.get(Dealership, contract.tenant_id).dealer_group_id
