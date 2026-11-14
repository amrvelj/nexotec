"""KAN-524 (FR-V-12; Anto: 2026-11-11 option A, 2026-11-13 Q1 B and Q2 A,
2026-11-14 Q3 A and Q4 A): the vehicle merge has its endpoint, Stock's items
follow it, and the Händlerschild assignment that had no caller is gone.

- `POST /v1/vehicle-mdm/{duplicate}/merge` — any user who may edit vehicles,
  If-Match on the duplicate and `survivorVersion` for the survivor; refusals
  before anything is written.
- `inventory.stock_items_on_vehicle_merged` moves every item naming the
  duplicate onto the survivor; a car in stock takes the survivor's VIN, and its
  configuration too (Q3 A); a sold row keeps its VIN.
- A merge is refused when the caller's own Betrieb holds a live item on each
  record; another Betrieb's double booking goes through and is reported by
  inventory's nightly reconciliation (Q4 A).

On Postgres; the consumer runs through the worker's own registration.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.reconciliation import ReconciliationAlarm
from app.inventory import reconciliation as inventory_reconciliation
from app.inventory.consumers import handle_vehicle_merged_message
from app.inventory.models.stock_item import StockItem
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.merge import MERGED_EVENT, merge_vehicles
from app.vehicle.services.stock_holding import VIN_ASSIGNED
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.test_inventory_vin_correction import _bought as _bought_with_configuration
from tests.test_vehicle_stock_holding import _bought, _deliver, _fed, _message, _token

SURVIVOR_VIN = "WVWZZZ1KZAW524100"
DUPLICATE_VIN = "WVWZZZ1KZAW524101"
CONSUMER = "inventory.stock_items_on_vehicle_merged"
TWO_LIVE = "two live stock items of one Betrieb name one vehicle_mdm record"


# --- helpers ---------------------------------------------------------------------


def _auth(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _record(db_session, vin: str) -> VehicleMdm:
    vehicle = create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
    db_session.commit()
    return vehicle


def _version(db_session, vehicle_id: uuid.UUID) -> int:
    db_session.expire_all()
    return db_session.get(VehicleMdm, vehicle_id).version


def _merge(client, token: str, db_session, duplicate_id, survivor_id, *, if_match=None, survivor_version=None):
    return client.post(
        f"/v1/vehicle-mdm/{duplicate_id}/merge",
        json={
            "survivorId": str(survivor_id),
            "survivorVersion": survivor_version if survivor_version is not None else _version(db_session, survivor_id),
        },
        headers=_auth(token, **{"If-Match": str(if_match if if_match is not None else _version(db_session, duplicate_id))}),
    )


def _deliver_merged(engine, db_session, duplicate_id: uuid.UUID) -> None:
    message = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT, OutboxMessage.aggregate_id == duplicate_id)
    ).one()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(MERGED_EVENT, consumer_name=CONSUMER, handler=handle_vehicle_merged_message)
    transport.deliver(message)
    db_session.expire_all()


def _merge_events(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT))


def _inventory_findings(db_session) -> set[tuple[str, uuid.UUID]]:
    try:
        inventory_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return {(orphan.check_label, orphan.source_row_id) for orphan in alarm.orphans}
    return set()


# --- the endpoint ------------------------------------------------------------------


def test_the_worker_registers_stocks_merge_consumer():
    from tests.test_consumer_redelivery_idempotency import _registered

    assert _registered()[CONSUMER] == (MERGED_EVENT, handle_vehicle_merged_message)


def test_a_merge_folds_the_duplicate_into_the_survivor_and_answers_the_survivor(client, db_session):
    dealership = _make_dealership(db_session)
    survivor = _record(db_session, SURVIVOR_VIN)
    duplicate = _record(db_session, DUPLICATE_VIN)
    version_before = duplicate.version

    response = _merge(client, _token(dealership.id), db_session, duplicate.id, survivor.id)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(survivor.id)
    db_session.expire_all()
    stored = db_session.get(VehicleMdm, duplicate.id)
    assert stored.merged_into_vehicle_id == survivor.id
    assert stored.version == version_before + 1
    assert _merge_events(db_session) == 1
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == duplicate.id, AuditEvent.action == "merge")
    ).one()
    assert audit.after["mergedIntoVehicleId"] == str(survivor.id)


def test_a_merge_is_refused_before_anything_is_written(client, db_session):
    dealership = _make_dealership(db_session)
    token = _token(dealership.id)
    survivor = _record(db_session, SURVIVOR_VIN)
    duplicate = _record(db_session, DUPLICATE_VIN)
    third = _record(db_session, "WVWZZZ1KZAW524102")
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=third.id, actor_id=uuid.uuid4())

    same = _merge(client, token, db_session, duplicate.id, duplicate.id)
    stale_duplicate = _merge(client, token, db_session, duplicate.id, survivor.id, if_match=99)
    stale_survivor = _merge(client, token, db_session, duplicate.id, survivor.id, survivor_version=99)
    into_merged = _merge(client, token, db_session, duplicate.id, third.id)
    of_merged = _merge(client, token, db_session, third.id, duplicate.id)
    unknown = _merge(client, token, db_session, duplicate.id, uuid.uuid4(), survivor_version=1)

    assert same.status_code == 422 and same.json()["error"]["details"]["reason"] == "vehicle_merge_same_record"
    assert stale_duplicate.status_code == 409 and stale_duplicate.json()["error"]["details"]["currentVersion"] is not None
    assert stale_survivor.status_code == 409 and stale_survivor.json()["error"]["details"]["currentVersion"] is not None
    for refused in (into_merged, of_merged):
        assert refused.status_code == 409, refused.text
        details = refused.json()["error"]["details"]
        assert details["reason"] == "vehicle_merged"
        assert details["mergedIntoVehicleId"] == str(survivor.id)
    assert unknown.status_code == 404
    db_session.expire_all()
    assert db_session.get(VehicleMdm, duplicate.id).merged_into_vehicle_id is None
    assert _merge_events(db_session) == 1  # the setup's merge only


def test_a_merge_needs_the_vehicle_write_right(client, db_session):
    from app.core.auth import AccessRole, create_access_token
    from tests.token_support import dealer_group_of

    dealership = _make_dealership(db_session)
    survivor = _record(db_session, SURVIVOR_VIN)
    duplicate = _record(db_session, DUPLICATE_VIN)
    reader = create_access_token(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealer_group_of(str(dealership.id)),
        roles=frozenset({AccessRole.AUDITOR}),
    )

    response = _merge(client, reader, db_session, duplicate.id, survivor.id)

    assert response.status_code == 403, response.text
    assert db_session.get(VehicleMdm, duplicate.id).merged_into_vehicle_id is None


def test_a_merge_is_refused_when_the_callers_betrieb_holds_both_records_in_stock(client, db_session):
    dealership = _make_dealership(db_session)
    on_duplicate = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    on_survivor = _bought(db_session, dealership.id, vin=SURVIVOR_VIN)

    response = _merge(client, _token(dealership.id), db_session, on_duplicate.vehicle_id, on_survivor.vehicle_id)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vehicle_merge_both_in_stock"
    assert {details["duplicateStockNumber"], details["survivorStockNumber"]} == {
        on_duplicate.stock_number,
        on_survivor.stock_number,
    }
    assert _merge_events(db_session) == 0


def test_another_betriebs_double_booking_never_refuses_and_is_reported_to_it(client, db_session, engine):
    other = _make_dealership(db_session)
    on_duplicate = _bought(db_session, other.id, vin=DUPLICATE_VIN)
    on_survivor = _bought(db_session, other.id, vin=SURVIVOR_VIN)
    caller = _make_dealership(db_session)

    response = _merge(client, _token(caller.id), db_session, on_duplicate.vehicle_id, on_survivor.vehicle_id)
    assert response.status_code == 200, response.text
    assert "stockNumber" not in response.text  # no other Betrieb's stock is named (rule 7)
    _deliver_merged(engine, db_session, on_duplicate.vehicle_id)

    moved = db_session.get(StockItem, on_duplicate.id)
    assert moved.vehicle_id == on_survivor.vehicle_id
    assert moved.vin == DUPLICATE_VIN  # the other item holds the survivor's VIN in that Betrieb
    findings = _inventory_findings(db_session)
    assert (TWO_LIVE, on_duplicate.id) in findings
    assert (TWO_LIVE, on_survivor.id) in findings


# --- Stock follows (Q2 A, Q3 A) ------------------------------------------------------


def test_the_stock_car_moves_to_the_survivor_and_takes_its_vin_with_its_configuration(client, db_session, engine):
    item, tenant_id = _bought_with_configuration(client, db_session)
    duplicate_id = uuid.UUID(item["vehicleId"])
    duplicate_vin = db_session.get(VehicleMdm, duplicate_id).vin
    survivor = _record(db_session, SURVIVOR_VIN)

    response = _merge(client, _token(tenant_id), db_session, duplicate_id, survivor.id)
    assert response.status_code == 200, response.text
    _deliver_merged(engine, db_session, duplicate_id)

    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    configuration = db_session.get(VehicleConfiguration, stored.configuration_id)
    assert (stored.vehicle_id, stored.vin) == (survivor.id, SURVIVOR_VIN)
    assert stored.version == item["version"] + 1
    assert (configuration.vin, configuration.vehicle_id) == (SURVIVOR_VIN, survivor.id)
    history = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == stored.id, AuditEvent.action == "vehicle_merged")
    ).one()
    assert history.before == {"vehicleId": str(duplicate_id), "vin": duplicate_vin}
    assert history.after["mergedVehicleId"] == str(duplicate_id)
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == configuration.id, AuditEvent.action == "vin_corrected_by_merge")
    ).one()
    # Nothing left for the nightly checks: neither a merged record nor a VIN
    # that differs (the actor checks are this fixture's token-only users).
    assert not {
        label for label, row in _inventory_findings(db_session) if row == stored.id and not label.endswith("-> user.id")
    }


def test_a_sold_row_moves_and_keeps_the_vin_it_was_sold_under(db_session, engine):
    from app.core.base import utcnow

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    item.left_stock_at = utcnow()
    db_session.commit()
    survivor = _record(db_session, SURVIVOR_VIN)
    duplicate_id = item.vehicle_id
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())

    _deliver_merged(engine, db_session, duplicate_id)

    stored = db_session.get(StockItem, item.id)
    assert (stored.vehicle_id, stored.vin) == (survivor.id, DUPLICATE_VIN)


def test_a_merge_delivered_after_a_later_one_lands_on_the_live_record(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    middle = _record(db_session, "WVWZZZ1KZAW524103")
    live = _record(db_session, SURVIVOR_VIN)
    duplicate_id = item.vehicle_id
    merge_vehicles(db_session, survivor_id=middle.id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    merge_vehicles(db_session, survivor_id=live.id, duplicate_id=middle.id, actor_id=uuid.uuid4())

    _deliver_merged(engine, db_session, duplicate_id)

    stored = db_session.get(StockItem, item.id)
    assert (stored.vehicle_id, stored.vin) == (live.id, SURVIVOR_VIN)


def test_reconciliation_reports_two_live_items_of_one_betrieb_on_one_record(db_session):
    dealership = _make_dealership(db_session)
    first = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    second = _bought(db_session, dealership.id, vin=SURVIVOR_VIN)
    sold = _bought(db_session, dealership.id, vin="WVWZZZ1KZAW524104")
    assert {(TWO_LIVE, first.id), (TWO_LIVE, second.id)}.isdisjoint(_inventory_findings(db_session))

    from app.core.base import utcnow

    second.vehicle_id = first.vehicle_id
    sold.vehicle_id = first.vehicle_id
    sold.left_stock_at = utcnow()
    db_session.commit()

    findings = {row for label, row in _inventory_findings(db_session) if label == TWO_LIVE}
    assert findings == {first.id, second.id}


# --- Vehicle's projection of the records a Betrieb holds (ADR-160) --------------------


def test_the_merge_moves_vehicles_stock_holding_rows_with_the_car(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    _fed(db_session, engine, item)
    survivor = _record(db_session, SURVIVOR_VIN)
    duplicate_id = item.vehicle_id

    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    db_session.expire_all()

    holding = db_session.scalars(select(VehicleStockHolding).where(VehicleStockHolding.stock_item_id == item.id)).one()
    assert holding.vehicle_id == survivor.id


def test_a_vin_assigned_fact_applied_after_the_merge_lands_on_the_survivor(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id, vin=DUPLICATE_VIN)
    survivor = _record(db_session, SURVIVOR_VIN)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=item.vehicle_id, actor_id=uuid.uuid4())

    _deliver(engine, _message(db_session, VIN_ASSIGNED, item.id))
    db_session.expire_all()

    holding = db_session.scalars(select(VehicleStockHolding).where(VehicleStockHolding.stock_item_id == item.id)).one()
    assert holding.vehicle_id == survivor.id


# --- pre-1981 chassis-number records (KAN-535) -------------------------------------


def _oldtimers_of_one_betrieb(client, db_session):
    """One Betrieb buys two pre-1981 cars of one make under two chassis numbers: two records."""

    from tests.test_inventory_purchase_promotes import _token as _betrieb_token
    from tests.test_kan535_chassis_number_purchase import _oldtimer, _purchase

    token = _betrieb_token(client)
    first = _purchase(client, token, _oldtimer(client, db_session, token, vin="1130101234567"), vin="1130101234567").json()
    second = _purchase(client, token, _oldtimer(client, db_session, token, vin="1130101234568"), vin="1130101234568").json()
    return token, first, second


def test_a_vin_record_and_a_chassis_number_record_are_never_merged(client, db_session):
    _token_of_betrieb, chassis_item, _second = _oldtimers_of_one_betrieb(client, db_session)
    vin_record = _record(db_session, SURVIVOR_VIN)
    caller = _token(_make_dealership(db_session).id)
    chassis_record_id = uuid.UUID(chassis_item["vehicleId"])

    into_chassis = _merge(client, caller, db_session, vin_record.id, chassis_record_id)
    into_vin = _merge(client, caller, db_session, chassis_record_id, vin_record.id)

    for refused in (into_chassis, into_vin):
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["details"]["reason"] == "vehicle_merge_identifier_kinds_differ"
    assert _merge_events(db_session) == 0


def test_another_betriebs_two_chassis_number_cars_never_block_the_merge_consumer(client, db_session, engine):
    _token_of_betrieb, on_duplicate, on_survivor = _oldtimers_of_one_betrieb(client, db_session)
    duplicate_id, survivor_id = uuid.UUID(on_duplicate["vehicleId"]), uuid.UUID(on_survivor["vehicleId"])
    caller = _token(_make_dealership(db_session).id)

    response = _merge(client, caller, db_session, duplicate_id, survivor_id)
    assert response.status_code == 200, response.text
    _deliver_merged(engine, db_session, duplicate_id)

    # One live chassis-number car per record and Betrieb is an index: the item stays
    # where it was (no dead letter), and the nightly KAN-706 check names it.
    message = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT, OutboxMessage.aggregate_id == duplicate_id)
    ).one()
    from app.core.processed_event_model import ProcessedEvent

    assert db_session.scalar(
        select(func.count()).select_from(ProcessedEvent).where(
            ProcessedEvent.event_id == message.id, ProcessedEvent.consumer_name == CONSUMER
        )
    ) == 1
    assert db_session.get(StockItem, uuid.UUID(on_duplicate["id"])).vehicle_id == duplicate_id
    assert db_session.get(StockItem, uuid.UUID(on_survivor["id"])).vehicle_id == survivor_id


def test_a_stock_car_that_takes_the_survivors_vin_publishes_stocks_vin_fact(client, db_session, engine):
    item, tenant_id = _bought_with_configuration(client, db_session)
    duplicate_id = uuid.UUID(item["vehicleId"])
    survivor = _record(db_session, SURVIVOR_VIN)
    assert _merge(client, _token(tenant_id), db_session, duplicate_id, survivor.id).status_code == 200

    _deliver_merged(engine, db_session, duplicate_id)

    [fact] = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.vin_corrected",
            OutboxMessage.aggregate_id == uuid.UUID(item["id"]),
        )
    ).all()
    assert fact.payload["vin"] == SURVIVOR_VIN
    assert fact.payload["vehicleId"] == str(survivor.id)
    assert fact.payload["correctedBy"] is None
