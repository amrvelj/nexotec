"""WP-5 PR-6: vehicle merge — one-way, re-points everything, no unmerge.

KAN-634 (ADR-131): the merge publishes `vehicle.vehicle_mdm.merged` and
customer re-points its party rows when it consumes the fact."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.reconciliation import find_rows_in_state
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.reconciliation import CHECKS as CUSTOMER_CHECKS
from app.customer.services.customer import allocate_vehicle_party
from app.customer.services.vehicle_merge import handle_vehicle_merged_message
from app.vehicle.services.merge import MERGED_EVENT, merge_vehicles, resolve_merged_id
from app.vehicle.services.vehicle_history import add_accessory, record_odometer_reading
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.plate_support import seed_plate
from tests.test_sales_lifecycle_reservation import _customer

TENANT_ID = uuid.uuid4()


def test_merge_repoints_plates_accessories_and_odometer_readings(db_session):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)

    plate = seed_plate(
        db_session, vehicle_id=duplicate.id, group_id=uuid.uuid4(), plate="ZH 284611", valid_from=dt.date(2026, 1, 1),
        recording_tenant_id=TENANT_ID,
    )
    accessory = add_accessory(
        db_session, vehicle_id=duplicate.id, accessory_type="towbar", description=None,
        valid_from=dt.date(2024, 1, 1), recording_tenant_id=TENANT_ID,
    )
    from app.vehicle.models.vehicle_history import OdometerSource

    reading = record_odometer_reading(
        db_session, vehicle_id=duplicate.id, value=10000, reading_date=dt.date(2026, 1, 1),
        source=OdometerSource.MANUAL, recording_tenant_id=TENANT_ID,
    )

    merged_survivor = merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    assert merged_survivor.id == survivor.id

    db_session.refresh(plate)
    db_session.refresh(accessory)
    db_session.refresh(reading)
    assert plate.vehicle_id == survivor.id
    assert accessory.vehicle_id == survivor.id
    assert reading.vehicle_id == survivor.id

    db_session.refresh(duplicate)
    assert duplicate.merged_into_vehicle_id == survivor.id


def test_merging_an_already_merged_vehicle_raises(db_session):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())

    another = create_vehicle_mdm(db_session, vin="1HGCM82633A004352", catalogue_variant_id=None)
    with pytest.raises(ValueError):
        merge_vehicles(db_session, survivor_id=another.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())


def test_resolve_merged_id_follows_the_chain(db_session):
    a = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    b = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    c = create_vehicle_mdm(db_session, vin="1HGCM82633A004352", catalogue_variant_id=None)

    merge_vehicles(db_session, survivor_id=b.id, duplicate_id=a.id, actor_id=uuid.uuid4())
    merge_vehicles(db_session, survivor_id=c.id, duplicate_id=b.id, actor_id=uuid.uuid4())

    assert resolve_merged_id(db_session, a.id) == c.id
    assert resolve_merged_id(db_session, c.id) == c.id


# --- KAN-634: the party links follow as a fact, never a call (ADR-131) ----------------


def _deliver_merged(engine, message: OutboxMessage) -> None:
    """Delivers one `vehicle.vehicle_mdm.merged` through the consumer the
    worker registers — on a session of its own, with its own commit."""

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(
        MERGED_EVENT, consumer_name="customer.vehicle_parties_on_vehicle_merged", handler=handle_vehicle_merged_message
    )
    transport.deliver(message)


def _merged_message(db, duplicate_id: uuid.UUID) -> OutboxMessage:
    return db.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT, OutboxMessage.aggregate_id == duplicate_id)
    ).one()


def _allocate(db, vehicle_id, group_id, role, **dates) -> VehicleParty:
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=_customer(db, group_id).id, role=role, group_id=group_id,
        actor_id=uuid.uuid4(), **dates,
    )


def test_the_worker_registers_the_vehicle_merged_consumer():
    from tests.test_consumer_redelivery_idempotency import _registered

    assert _registered()["customer.vehicle_parties_on_vehicle_merged"] == (MERGED_EVENT, handle_vehicle_merged_message)


def test_a_merge_publishes_its_fact_and_leaves_the_party_rows_to_customer(db_session):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    group_id = uuid.uuid4()
    party = _allocate(db_session, duplicate.id, group_id, VehiclePartyRole.OWNER)

    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())

    message = _merged_message(db_session, duplicate.id)
    assert message.tenant_id is None  # vehicle-mdm is global (ADR-022)
    assert message.producer == "vehicle"
    assert message.aggregate_type == "vehicle_mdm"
    assert message.payload == {"vehicleId": str(duplicate.id), "mergedIntoVehicleId": str(survivor.id)}
    # The merge's own commit wrote no party row: until the fact is consumed
    # the holder still names the duplicate (ADR-047 — separate commits).
    db_session.expire_all()
    assert db_session.get(VehicleParty, party.id).vehicle_id == duplicate.id


def test_merging_two_vehicles_with_parties_on_both_moves_every_row_to_the_survivor(db_session, engine):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    survivor_owner = _allocate(db_session, survivor.id, group_a, VehiclePartyRole.OWNER)
    duplicate_keeper = _allocate(db_session, duplicate.id, group_a, VehiclePartyRole.KEEPER)
    ended = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    duplicate_former = _allocate(
        db_session, duplicate.id, group_b, VehiclePartyRole.OWNER,
        effective_from=dt.datetime(2024, 1, 1, tzinfo=dt.UTC), effective_to=ended,
    )
    duplicate_driver = _allocate(db_session, duplicate.id, group_b, VehiclePartyRole.DRIVER)
    survivor_owner_before = (survivor_owner.updated_at, survivor_owner.effective_to, survivor_owner.vehicle_vin)
    moved_ids = {duplicate_keeper.id, duplicate_former.id, duplicate_driver.id}
    assert {duplicate_keeper.vehicle_vin, duplicate_former.vehicle_vin} == {duplicate.vin}

    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    _deliver_merged(engine, _merged_message(db_session, duplicate.id))

    db_session.expire_all()
    rows = {row.id: row for row in db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == survivor.id))}
    assert set(rows) == moved_ids | {survivor_owner.id}
    assert db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == duplicate.id)).all() == []
    for row in rows.values():
        # KAN-84: the moved rows carry the survivor's label, not the duplicate's.
        assert (row.vehicle_vin, row.vehicle_number) == (survivor.vin, survivor.vehicle_number)
    # The move closes and reopens nothing: the history row keeps its end, the
    # open rows stay open, and the survivor's own holder is left as it was.
    assert rows[duplicate_former.id].effective_to == ended
    assert rows[duplicate_keeper.id].effective_to is None
    assert rows[duplicate_driver.id].effective_to is None
    owner = rows[survivor_owner.id]
    assert (owner.updated_at, owner.effective_to, owner.vehicle_vin) == survivor_owner_before


def test_a_merge_delivered_after_a_later_one_lands_on_the_live_record(db_session, engine):
    """Delivery is unordered: A→B consumed after B→C must put A's holders on
    C, never on the merged B."""

    a = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    b = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    c = create_vehicle_mdm(db_session, vin="1HGCM82633A004352", catalogue_variant_id=None)
    group_id = uuid.uuid4()
    on_a = _allocate(db_session, a.id, group_id, VehiclePartyRole.OWNER)
    on_b = _allocate(db_session, b.id, group_id, VehiclePartyRole.KEEPER)

    merge_vehicles(db_session, survivor_id=b.id, duplicate_id=a.id, actor_id=uuid.uuid4())
    merge_vehicles(db_session, survivor_id=c.id, duplicate_id=b.id, actor_id=uuid.uuid4())
    _deliver_merged(engine, _merged_message(db_session, b.id))
    _deliver_merged(engine, _merged_message(db_session, a.id))

    db_session.expire_all()
    for party_id in (on_a.id, on_b.id):
        row = db_session.get(VehicleParty, party_id)
        assert (row.vehicle_id, row.vehicle_vin) == (c.id, c.vin)


def test_a_merge_of_a_vehicle_no_party_names_writes_nothing(db_session, engine):
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    held = _allocate(db_session, survivor.id, uuid.uuid4(), VehiclePartyRole.OWNER)
    stamped = held.vehicle_label_refreshed_at

    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    _deliver_merged(engine, _merged_message(db_session, duplicate.id))

    db_session.expire_all()
    assert db_session.get(VehicleParty, held.id).vehicle_label_refreshed_at == stamped


def test_reconciliation_reports_a_party_row_left_on_a_merged_vehicle(db_session, engine):
    """Rule 10: between the merge's commit and the consumer's — or for good,
    when the delivery dead-letters — a holder still names the merged record;
    the nightly customer check names it until the fact is consumed."""

    check = next(c for c in CUSTOMER_CHECKS if c.label == "vehicle_party.vehicle_id names a merged vehicle")
    survivor = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None)
    party = _allocate(db_session, duplicate.id, uuid.uuid4(), VehiclePartyRole.OWNER)
    assert find_rows_in_state(db_session, check) == []

    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    assert find_rows_in_state(db_session, check) == [(party.id, party.id)]

    _deliver_merged(engine, _merged_message(db_session, duplicate.id))
    db_session.expire_all()
    assert find_rows_in_state(db_session, check) == []
