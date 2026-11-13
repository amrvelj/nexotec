"""KAN-706 — a vehicle merge commits in Vehicle first; customer moves the
party rows later (`vehicle.vehicle_mdm.merged`, retried while a role is busy).
A trade-in fact consumed in between, and a stock hold booked on the duplicate,
must give the same answer as when the move had already run (ADR-160 amended).

1. The trade-in purchase and its returned storno read the customer's rows on
   every record of the merge chain, under all their locks: either delivery
   order leaves the same history.
2. The stock-holding read counts a hold on a record merged into the one asked,
   so the survivor's VIN change is refused as the duplicate's would be.
3. Inventory's reconciliation names a stock item still naming a merged record.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.core.base import utcnow
from app.core.reconciliation import find_rows_in_state
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.trade_in_holders import record_former_holder_on_purchase, reopen_former_holder_after_storno
from app.customer.services.vehicle_party import allocate_vehicle_party
from app.inventory.reconciliation import CHECKS as INVENTORY_CHECKS
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import merged_vehicle_chain
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.merge_chain import records_living_on
from app.vehicle.services.stock_holding import held_stock_item
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.test_sales_lifecycle_reservation import _customer
from tests.test_vehicle_merge import _deliver_merged, _merged_message
from tests.test_vehicle_stock_holding import REFUSED, _bought, _fed, _patch, _token

OWNER, KEEPER = VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER
SURVIVOR_VIN, DUPLICATE_VIN = "ZAR94000007706001", "WVWZZZ1JZXW706002"


def _two_cars(db):
    survivor = create_vehicle_mdm(db, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db, vin=DUPLICATE_VIN, catalogue_variant_id=None)
    db.commit()
    return survivor.id, duplicate.id


def _history(db, customer_id) -> set[tuple]:
    """The customer's party rows as a dealer reads them: where each lives now,
    its role, its period, and whether it is a merge's kept copy."""

    db.expire_all()
    rows = db.scalars(select(VehicleParty).where(VehicleParty.customer_id == customer_id)).all()
    return {
        (row.vehicle_id, row.role, row.effective_from, row.effective_to, row.merged_into_vehicle_id is not None)
        for row in rows
    }


def _a_customer_holding_the_duplicate(db, *, duplicate_id, group_id, since):
    """The customer linked as owner and keeper of the record that later turns
    out to be the duplicate — open rows, as a dealer links a customer's car."""

    customer = _customer(db, group_id)
    db.commit()
    for role in (OWNER, KEEPER):
        allocate_vehicle_party(
            db, vehicle_id=duplicate_id, customer_id=customer.id, role=role, group_id=group_id,
            actor_id=uuid.uuid4(), effective_from=since,
        )
    db.commit()
    return customer


def _purchase(db, *, vehicle_id, customer_id, group_id, held_from, ended_at):
    record_former_holder_on_purchase(
        db, vehicle_id=vehicle_id, customer_id=customer_id, group_id=group_id,
        held_from=held_from, ended_at=ended_at, actor_id=None,
    )
    db.commit()


# --- 1. the trade-in consumers, in both orders ------------------------------------


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("order", ["move_first", "purchase_first"])
def test_a_purchase_gives_the_same_history_whether_or_not_the_party_move_ran(db_session, engine, order):
    """The customer held the car (on the record Stock booked it under) since
    long before the signature. Whichever of the purchase and the merge's party
    move is consumed first, the customer's rows end at the purchase and nothing
    is left open: the customer is not the holder of a car the dealer bought."""

    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    since = utcnow() - dt.timedelta(days=400)
    customer = _a_customer_holding_the_duplicate(db_session, duplicate_id=duplicate_id, group_id=group_id, since=since)
    ended_at = utcnow() - dt.timedelta(days=1)
    held_from = ended_at - dt.timedelta(days=30)

    merge_vehicles(db_session, survivor_id=survivor_id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    db_session.commit()
    message = _merged_message(db_session, duplicate_id)
    if order == "move_first":
        _deliver_merged(engine, message)
        _purchase(db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
                  held_from=held_from, ended_at=ended_at)
    else:
        _purchase(db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
                  held_from=held_from, ended_at=ended_at)
        _deliver_merged(engine, message)

    assert _history(db_session, customer.id) == {
        (survivor_id, OWNER, since, ended_at, False),
        (survivor_id, KEEPER, since, ended_at, False),
    }


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("order", ["move_first", "storno_first"])
def test_a_returned_storno_reopens_the_same_rows_whether_or_not_the_party_move_ran(db_session, engine, order):
    """The purchase closed the customer's rows on the record Stock booked.
    The merge commits; the car goes back to the customer. Whichever of the
    storno and the party move is consumed first, the same rows end up open on
    the survivor."""

    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    db_session.commit()
    signed_at = utcnow() - dt.timedelta(days=10)
    ended_at = utcnow() - dt.timedelta(days=2)
    _purchase(db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
              held_from=signed_at, ended_at=ended_at)

    merge_vehicles(db_session, survivor_id=survivor_id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    db_session.commit()
    message = _merged_message(db_session, duplicate_id)

    def storno() -> list[VehicleParty]:
        reopened = reopen_former_holder_after_storno(
            db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
            closed_after=signed_at, closed_until=utcnow(), actor_id=None,
        )
        db_session.commit()
        return reopened

    if order == "move_first":
        _deliver_merged(engine, message)
        reopened = storno()
    else:
        reopened = storno()
        _deliver_merged(engine, message)

    assert {row.role for row in reopened} == {OWNER, KEEPER}
    assert _history(db_session, customer.id) == {
        (survivor_id, OWNER, signed_at, None, False),
        (survivor_id, KEEPER, signed_at, None, False),
    }


def test_the_chain_runs_from_the_named_record_to_the_live_one(db_session):
    a = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW706011", catalogue_variant_id=None)
    b = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW706012", catalogue_variant_id=None)
    c = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW706013", catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=b.id, duplicate_id=a.id, actor_id=uuid.uuid4())
    merge_vehicles(db_session, survivor_id=c.id, duplicate_id=b.id, actor_id=uuid.uuid4())

    assert merged_vehicle_chain(db_session, a.id) == (a.id, b.id, c.id)
    assert merged_vehicle_chain(db_session, c.id) == (c.id,)
    assert set(records_living_on(db_session, c.id)) == {a.id, b.id, c.id}
    assert records_living_on(db_session, a.id) == (a.id,)
    unknown = uuid.uuid4()
    assert merged_vehicle_chain(db_session, unknown) == (unknown,)


# --- 2. the stock hold follows the merge -------------------------------------------


def test_a_hold_on_a_merged_duplicate_refuses_the_survivors_vin_change(client, db_session, engine):
    """Stock booked the car on the record that turned out to be the duplicate.
    After the merge, a VIN change on the survivor is refused with that stock
    car, as it was on the duplicate before."""

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    survivor = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=item.vehicle_id, actor_id=uuid.uuid4())
    db_session.commit()

    held = held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=survivor.id)
    response = _patch(client, _token(dealership.id), survivor.id, {"vin": "WVWZZZ1JZXW706099"})

    assert held is not None and held.stock_item_id == item.id
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == REFUSED
    assert response.json()["error"]["details"]["stockItemId"] == str(item.id)
    # Another Betrieb's stock on the duplicate still counts for nobody else (rule 7).
    assert held_stock_item(db_session, tenant_id=uuid.uuid4(), vehicle_id=survivor.id) is None


def test_a_fact_in_flight_on_a_merged_duplicate_refuses_the_survivors_vin_change(db_session):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)  # its vin_assigned fact is still pending
    survivor = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=item.vehicle_id, actor_id=uuid.uuid4())
    db_session.commit()

    held = held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=survivor.id)

    assert held is not None and held.stock_item_id == item.id


# --- 3. reconciliation names the item ----------------------------------------------


def test_reconciliation_names_a_stock_item_left_on_a_merged_record(db_session):
    check = next(c for c in INVENTORY_CHECKS if c.label == "stock_item.vehicle_id names a merged vehicle_mdm record")
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    survivor = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=item.vehicle_id, actor_id=uuid.uuid4())
    db_session.commit()

    # A merge within the outbox-lag grace is not a finding yet …
    assert find_rows_in_state(db_session, check) == []

    db_session.execute(
        update(VehicleMdm).where(VehicleMdm.id == item.vehicle_id).values(updated_at=utcnow() - dt.timedelta(hours=2))
    )
    db_session.commit()

    # … an older one is, and an item on a live record never is.
    assert find_rows_in_state(db_session, check) == [(item.id, item.id)]
