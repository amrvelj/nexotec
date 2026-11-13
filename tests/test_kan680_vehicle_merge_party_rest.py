"""KAN-680 — vehicle merge × party rows, the rest (KAN-672 / KAN-673 follow-up).

1. The trade-in purchase (`record_former_holder_on_purchase`) and its storno
   (`reopen_former_holder_after_storno`) follow a merged vehicle-mdm record to
   the one it lives on and take that record's (vehicle, role, group) locks, as
   an allocation does since KAN-673 — so no row lands on the duplicate. Since
   KAN-706 they take the duplicate's locks too (`test_kan706_*`).
2. The History names `mergedIntoVehicleId` (backend keys; the frontend's
   `REFERENCE_KEYS` are held equal by `test_history_reference_keys.py`).
3. Anto, 2026-11-13, option A: reopening a vehicle-merge copy is refused with
   its own reason, naming the surviving record and its VIN; the Vehicles tab's
   page carries both so the tab marks the copy.
4. Item 5: a PATCH or a Disconnect that read its row before a vehicle merge
   moved it takes the surviving record's lock too.
"""

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.schemas.customer import CustomerVehicleUpdate
from app.customer.services import customer as customer_service
from app.customer.services.trade_in_holders import record_former_holder_on_purchase, reopen_former_holder_after_storno
from app.customer.services.vehicle_page import customer_vehicle_page
from app.customer.services.vehicle_party import (
    allocate_vehicle_party,
    delete_customer_vehicle,
    update_customer_vehicle,
)
from app.customer.services.vehicle_party_locks import _lock_vehicle_role_in_group
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_sales_lifecycle_reservation import _customer
from tests.test_vehicle_merge import _deliver_merged, _merged_message

OWNER, KEEPER = VehiclePartyRole.OWNER, VehiclePartyRole.KEEPER
SURVIVOR_VIN, DUPLICATE_VIN = "ZAR94000007680001", "WVWZZZ1JZXW680002"


def _two_cars(db):
    survivor = create_vehicle_mdm(db, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db, vin=DUPLICATE_VIN, catalogue_variant_id=None)
    db.commit()
    return survivor.id, duplicate.id


def _merge(db, engine, *, survivor_id, duplicate_id) -> None:
    """The merge and its consumed fact: the party rows that named the
    duplicate have moved, and nothing moves a row written on it later."""

    merge_vehicles(db, survivor_id=survivor_id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    db.commit()
    _deliver_merged(engine, _merged_message(db, duplicate_id))
    db.expire_all()


def _rows(db, vehicle_id) -> list[VehicleParty]:
    db.expire_all()
    return list(db.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == vehicle_id).order_by(VehicleParty.id)))


def _purchase(db, *, vehicle_id, customer_id, group_id):
    ended_at = utcnow() - dt.timedelta(days=1)
    return record_former_holder_on_purchase(
        db, vehicle_id=vehicle_id, customer_id=customer_id, group_id=group_id,
        held_from=ended_at - dt.timedelta(days=200), ended_at=ended_at, actor_id=None,
    )


# --- 1. the trade-in consumers follow the merge ------------------------------


@NEEDS_ITS_OWN_CONNECTION
def test_a_purchase_naming_a_merged_record_is_recorded_on_its_survivor(db_session, engine):
    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    db_session.commit()
    _merge(db_session, engine, survivor_id=survivor_id, duplicate_id=duplicate_id)

    written = _purchase(db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id)
    db_session.commit()

    assert {(row.vehicle_id, row.role) for row in written} == {(survivor_id, OWNER), (survivor_id, KEEPER)}
    assert _rows(db_session, duplicate_id) == []
    assert {row.role for row in _rows(db_session, survivor_id)} == {OWNER, KEEPER}


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("locked", ["survivor", "duplicate"])
def test_the_purchase_takes_the_locks_of_both_records(db_session, engine, locked):
    """KAN-680 took the survivor's locks only. KAN-706 (ADR-160 amended): the
    purchase cannot tell whether the merge's party move has run, so it takes
    the locks of every record of the chain — the duplicate's too — and the
    move, which holds both, waits for it or it for the move."""

    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    db_session.commit()
    _merge(db_session, engine, survivor_id=survivor_id, duplicate_id=duplicate_id)
    held_vehicle = survivor_id if locked == "survivor" else duplicate_id
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as holder, factory() as writer:
        _lock_vehicle_role_in_group(holder, vehicle_id=held_vehicle, role=KEEPER, group_id=group_id)
        writer.execute(text("SET LOCAL lock_timeout = '300ms'"))
        try:
            with pytest.raises(OperationalError, match="lock timeout"):
                _purchase(writer, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id)
        finally:
            holder.rollback()
            writer.rollback()


@NEEDS_ITS_OWN_CONNECTION
def test_a_returned_storno_naming_a_merged_record_reopens_the_rows_on_its_survivor(db_session, engine):
    """The purchase closed the rows on the record Stock booked; the vehicle
    merge then moved them onto the survivor. The storno still names the
    duplicate, and reopens the moved rows where they are now."""

    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    db_session.commit()
    signed_at = utcnow() - dt.timedelta(days=10)
    record_former_holder_on_purchase(
        db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
        held_from=signed_at, ended_at=utcnow() - dt.timedelta(days=2), actor_id=None,
    )
    db_session.commit()
    _merge(db_session, engine, survivor_id=survivor_id, duplicate_id=duplicate_id)
    assert {row.role for row in _rows(db_session, survivor_id)} == {OWNER, KEEPER}

    reopened = reopen_former_holder_after_storno(
        db_session, vehicle_id=duplicate_id, customer_id=customer.id, group_id=group_id,
        closed_after=signed_at, closed_until=utcnow(), actor_id=None,
    )
    db_session.commit()

    assert {(row.vehicle_id, row.role) for row in reopened} == {(survivor_id, OWNER), (survivor_id, KEEPER)}
    assert all(row.effective_to is None for row in _rows(db_session, survivor_id))
    assert _rows(db_session, duplicate_id) == []


# --- 2. the History names the merged-into record -------------------------------


def test_the_history_names_merged_into_vehicle_id_by_its_vehicle(db_session):
    group_id = uuid.uuid4()
    car = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    customer = _customer(db_session, group_id)
    db_session.add(
        VehicleParty(
            vehicle_id=car.id, customer_id=customer.id, role=OWNER, effective_from=utcnow(),
            vehicle_make="Fiat", vehicle_model="500", vehicle_model_year=1972,
        )
    )
    db_session.commit()
    event = SimpleNamespace(actor_id=None, before=None, after={"mergedIntoVehicleId": str(car.id)})

    labels = customer_service.history_labels(db_session, group_id=group_id, dealership_id=uuid.uuid4(), events=[event])

    assert labels == {str(car.id): "Fiat 500 (1972)"}


# --- 3. the copy: its own refusal, and the mark on the Vehicles tab ----------


def _copy_on_duplicate(db, *, survivor_id, duplicate_id, group_id):
    customer = _customer(db, group_id)
    start = utcnow() - dt.timedelta(days=30)
    copy = VehicleParty(
        vehicle_id=duplicate_id, customer_id=customer.id, role=OWNER, effective_from=start, effective_to=start,
        merged_into_vehicle_id=survivor_id,
    )
    db.add(copy)
    db.commit()
    return customer, copy


def test_reopening_a_vehicle_merge_copy_is_refused_with_its_own_reason(db_session):
    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    _, copy = _copy_on_duplicate(db_session, survivor_id=survivor_id, duplicate_id=duplicate_id, group_id=group_id)

    with pytest.raises(ConflictError) as refused:
        update_customer_vehicle(
            db_session, party=copy, data=CustomerVehicleUpdate(effective_to=None), actor_id=uuid.uuid4(),
            group_id=group_id,
        )

    assert refused.value.details == {
        "reason": "vehicle_merged_party_row",
        "mergedIntoVehicleId": str(survivor_id),
        "mergedIntoVehicleVin": SURVIVOR_VIN,
    }
    db_session.rollback()
    assert db_session.get(VehicleParty, copy.id).effective_to is not None


def test_the_vehicles_page_marks_the_copy_with_the_surviving_record_and_its_vin(db_session):
    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer, copy = _copy_on_duplicate(
        db_session, survivor_id=survivor_id, duplicate_id=duplicate_id, group_id=group_id
    )
    own = allocate_vehicle_party(
        db_session, vehicle_id=survivor_id, customer_id=customer.id, role=KEEPER, group_id=group_id,
        actor_id=uuid.uuid4(),
    )
    db_session.commit()

    page = customer_vehicle_page(
        db_session, group_id=group_id, customer_id=customer.id, include_closed=True, stock_items_for=lambda ids: {}
    )

    by_id = {item.id: item for item in page.items}
    assert (by_id[copy.id].merged_into_vehicle_id, by_id[copy.id].merged_into_vehicle_vin) == (
        survivor_id,
        SURVIVOR_VIN,
    )
    assert (by_id[own.id].merged_into_vehicle_id, by_id[own.id].merged_into_vehicle_vin) == (None, None)
    dumped = by_id[copy.id].model_dump(by_alias=True, mode="json")
    assert dumped["mergedIntoVehicleId"] == str(survivor_id)
    assert dumped["mergedIntoVehicleVin"] == SURVIVOR_VIN


# --- 4. item 5: a row moved while the write waited --------------------------


def _row_read_then_moved(db_session, engine, writer):
    """A Disconnect or PATCH read its row (on the duplicate); a vehicle merge
    and its consumer then moved the row onto the survivor and committed."""

    survivor_id, duplicate_id = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _customer(db_session, group_id)
    party = allocate_vehicle_party(
        db_session, vehicle_id=duplicate_id, customer_id=customer.id, role=OWNER, group_id=group_id,
        actor_id=uuid.uuid4(),
    )
    db_session.commit()
    stale = writer.get(VehicleParty, party.id)
    assert stale.vehicle_id == duplicate_id
    _merge(db_session, engine, survivor_id=survivor_id, duplicate_id=duplicate_id)
    assert db_session.get(VehicleParty, party.id).vehicle_id == survivor_id
    return SimpleNamespace(survivor=survivor_id, duplicate=duplicate_id, group=group_id, party=stale, party_id=party.id)


@NEEDS_ITS_OWN_CONNECTION
def test_a_disconnect_of_a_row_moved_meanwhile_closes_it_on_the_survivor(db_session, engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as writer:
        state = _row_read_then_moved(db_session, engine, writer)
        delete_customer_vehicle(writer, party=state.party, actor_id=uuid.uuid4(), group_id=state.group)

    db_session.expire_all()
    row = db_session.get(VehicleParty, state.party_id)
    assert (row.vehicle_id, row.effective_to is not None) == (state.survivor, True)


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("write", ["disconnect", "patch"])
def test_a_write_on_a_row_moved_meanwhile_takes_the_survivors_lock(db_session, engine, write):
    """The survivor's lock is held by another writer: the write is refused
    (409 `vehicle_merge_in_progress`, nothing written) instead of changing a
    survivor row behind that writer's back."""

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as writer, factory() as holder:
        state = _row_read_then_moved(db_session, engine, writer)
        _lock_vehicle_role_in_group(holder, vehicle_id=state.survivor, role=OWNER, group_id=state.group)
        try:
            with pytest.raises(ConflictError) as refused:
                if write == "disconnect":
                    delete_customer_vehicle(writer, party=state.party, actor_id=uuid.uuid4(), group_id=state.group)
                else:
                    update_customer_vehicle(
                        writer, party=state.party, data=CustomerVehicleUpdate(effective_to=None),
                        actor_id=uuid.uuid4(), group_id=state.group,
                    )
            assert refused.value.details == {"reason": "vehicle_merge_in_progress"}
        finally:
            writer.rollback()
            holder.rollback()

    db_session.expire_all()
    assert db_session.get(VehicleParty, state.party_id).effective_to is None
