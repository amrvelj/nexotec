"""KAN-724 (ADR-047 addendum; ADR-160's KAN-524 amendment): Stock's daily repair
`inventory.merged_record_items.repair` moves a live stock item the merge
consumer had to leave on a merged record — its Betrieb held the survivor live
under a chassis number, which `uq_stock_item_tenant_id_chassis_vehicle_in_stock`
allows once per record and Betrieb (KAN-535) — onto the live record once
nothing blocks it, with the consumer's audit row; a still-blocked item stays.

The blocker leaves stock through Stock's one writer of `left_stock_at`,
`apply_finance_invoice_issued` (no production caller until WP-9: until then
nothing unblocks such an item, and the repair finds it blocked every night).
"""

import logging
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.inventory.daily_jobs import run_daily_merged_record_repair
from app.inventory.models.stock_item import StockItem
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.vehicle_merge import repair_items_left_on_merged_records
from app.vehicle.public import find_merged_vehicle_ids
from app.vehicle.services.merge import MERGED_EVENT
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.vehicle.test_kan524_vehicle_merge import _deliver_merged, _merge, _oldtimers_of_one_betrieb, _record
from tests.vehicle.test_vehicle_stock_holding import _token


def _left_on_the_duplicate(client, db_session, engine):
    """Two live chassis-number cars of one Betrieb, their records merged by
    another Betrieb's user: the duplicate's car stays where it was (KAN-524)."""

    _token_of_betrieb, on_duplicate, on_survivor = _oldtimers_of_one_betrieb(client, db_session)
    duplicate_id, survivor_id = uuid.UUID(on_duplicate["vehicleId"]), uuid.UUID(on_survivor["vehicleId"])
    caller = _token(_make_dealership(db_session).id)
    response = _merge(client, caller, db_session, duplicate_id, survivor_id)
    assert response.status_code == 200, response.text
    _deliver_merged(engine, db_session, duplicate_id)
    left = db_session.get(StockItem, uuid.UUID(on_duplicate["id"]))
    blocker = db_session.get(StockItem, uuid.UUID(on_survivor["id"]))
    assert left.vehicle_id == duplicate_id and left.left_stock_at is None
    return left.id, blocker.id, duplicate_id, survivor_id


def test_a_still_blocked_item_stays_and_is_counted(client, db_session, engine):
    left_id, _blocker_id, duplicate_id, _survivor_id = _left_on_the_duplicate(client, db_session, engine)

    result = repair_items_left_on_merged_records(db_session)

    assert (result.records, result.moved, result.still_blocked, result.failed) == (1, 0, 1, ())
    db_session.expire_all()
    assert db_session.get(StockItem, left_id).vehicle_id == duplicate_id


def test_the_left_item_follows_once_its_betrieb_has_sold_the_other_car(client, db_session, engine):
    left_id, blocker_id, duplicate_id, survivor_id = _left_on_the_duplicate(client, db_session, engine)
    blocker = db_session.get(StockItem, blocker_id)
    blocker.is_invoiceable = True  # the purchase booking's fact; WP-9's invoice is what sells it
    db_session.flush()
    apply_finance_invoice_issued(db_session, tenant_id=blocker.tenant_id, stock_item_id=blocker_id, invoice_ref="R-1")
    db_session.commit()
    before = db_session.get(StockItem, left_id)
    version = before.version
    survivor_number = db_session.get(StockItem, blocker_id).vin

    result = repair_items_left_on_merged_records(db_session)

    assert (result.records, result.moved, result.still_blocked, result.failed) == (1, 1, 0, ())
    db_session.expire_all()
    moved = db_session.get(StockItem, left_id)
    assert moved.vehicle_id == survivor_id
    assert moved.version == version + 1
    # As the consumer would have (Q3 A): nothing of its Betrieb holds the survivor live any
    # more, so the car takes the survivor's number — the duplicate came from a typo.
    assert moved.vin == survivor_number
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == left_id, AuditEvent.action == "vehicle_merged")
    ).one()
    assert audit.actor_id is None and audit.reason == "vehicle_merged"
    assert audit.after["mergedVehicleId"] == str(duplicate_id)

    # A second night finds nothing left on a merged record.
    again = repair_items_left_on_merged_records(db_session)
    assert (again.records, again.moved, again.still_blocked) == (0, 0, 0)


def test_the_repair_takes_the_survivors_vin_as_the_consumer_does(client, db_session, engine):
    """The same move as the consumer's: a car left on a merged record whose live record has a VIN
    takes that VIN and publishes Stock's VIN fact. Here the consumer never ran (a dead letter)."""

    from tests.vehicle.test_kan524_vehicle_merge import SURVIVOR_VIN

    token_of_betrieb, chassis_item, _second = _oldtimers_of_one_betrieb(client, db_session)
    vin_record = _record(db_session, SURVIVOR_VIN)
    chassis_record_id = uuid.UUID(chassis_item["vehicleId"])
    response = _merge(client, token_of_betrieb, db_session, chassis_record_id, vin_record.id)
    assert response.status_code == 200, response.text
    # The merge fact is never delivered: the item still names the merged record.
    assert db_session.get(StockItem, uuid.UUID(chassis_item["id"])).vehicle_id == chassis_record_id

    result = repair_items_left_on_merged_records(db_session)

    assert (result.records, result.moved, result.still_blocked) == (1, 1, 0)
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(chassis_item["id"]))
    assert (stored.vehicle_id, stored.vin) == (vin_record.id, SURVIVOR_VIN)
    facts = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.vin_corrected", OutboxMessage.aggregate_id == stored.id
        )
    ).all()
    assert len(facts) == 1 and facts[0].payload["vin"] == SURVIVOR_VIN
    assert db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT)).all()


def test_find_merged_vehicle_ids_names_only_merged_records(client, db_session, engine):
    _left_id, _blocker_id, duplicate_id, survivor_id = _left_on_the_duplicate(client, db_session, engine)

    assert find_merged_vehicle_ids(db_session, [duplicate_id, survivor_id, uuid.uuid4()]) == {duplicate_id}
    assert find_merged_vehicle_ids(db_session, []) == set()


def test_the_daily_job_logs_its_counts(client, db_session, engine, caplog):
    _left_on_the_duplicate(client, db_session, engine)

    with caplog.at_level(logging.INFO, logger="app.inventory.daily_jobs"):
        run_daily_merged_record_repair(db_session)

    assert "inventory.merged_record_items.repair: records=1 moved=0 still_blocked=1 failed=0" in caplog.text


def test_the_worker_registers_the_repair_after_reconciliation():
    from app.core import daily_scheduler
    from app.worker import register_daily_jobs

    saved = dict(daily_scheduler._REGISTRY)
    daily_scheduler._REGISTRY.clear()
    try:
        register_daily_jobs()
        names = list(daily_scheduler._REGISTRY)
    finally:
        daily_scheduler._REGISTRY.clear()
        daily_scheduler._REGISTRY.update(saved)
    assert names.index("inventory.merged_record_items.repair") > names.index("reconciliation.run_all")


def test_a_failing_record_is_rolled_back_counted_and_never_stops_the_others(client, db_session, engine, monkeypatch):
    """Two merged records each holding a live item the consumer never moved: the first record's
    move raises — it is rolled back and counted as failed, not as blocked — and the second
    still moves and commits."""

    from app.inventory.services import vehicle_merge
    from tests.vehicle.test_kan524_vehicle_merge import SURVIVOR_VIN

    token_of_betrieb, first, second = _oldtimers_of_one_betrieb(client, db_session)
    survivors = [_record(db_session, SURVIVOR_VIN), _record(db_session, SURVIVOR_VIN.replace("100", "102"))]
    records = [uuid.UUID(first["vehicleId"]), uuid.UUID(second["vehicleId"])]
    for record_id, survivor in zip(records, survivors, strict=True):
        assert _merge(client, token_of_betrieb, db_session, record_id, survivor.id).status_code == 200
    failing = min(records)  # the repair walks the records in id order
    real_move = vehicle_merge.move_stock_items_on_vehicle_merge

    def move(db, *, duplicate_vehicle_id, survivor_vehicle_id):
        moved = real_move(db, duplicate_vehicle_id=duplicate_vehicle_id, survivor_vehicle_id=survivor_vehicle_id)
        if duplicate_vehicle_id == failing:
            raise RuntimeError("the move failed after writing")
        return moved

    monkeypatch.setattr(vehicle_merge, "move_stock_items_on_vehicle_merge", move)

    result = repair_items_left_on_merged_records(db_session)

    assert (result.records, result.moved, result.still_blocked, result.failed) == (2, 1, 0, (failing,))
    db_session.expire_all()
    by_record = {uuid.UUID(item["vehicleId"]): uuid.UUID(item["id"]) for item in (first, second)}
    assert db_session.get(StockItem, by_record[failing]).vehicle_id == failing  # rolled back
    other = next(record_id for record_id in records if record_id != failing)
    assert db_session.get(StockItem, by_record[other]).vehicle_id != other  # moved and committed


def test_the_daily_job_logs_a_failed_record_at_error(client, db_session, engine, monkeypatch, caplog):
    from app.inventory.services import vehicle_merge

    _left_id, _blocker_id, duplicate_id, _survivor_id = _left_on_the_duplicate(client, db_session, engine)

    def move(db, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(vehicle_merge, "move_stock_items_on_vehicle_merge", move)
    with caplog.at_level(logging.INFO, logger="app.inventory.daily_jobs"):
        run_daily_merged_record_repair(db_session)

    errors = [record for record in caplog.records if record.name == "app.inventory.daily_jobs"]
    assert errors and errors[-1].levelno == logging.ERROR
    assert f"failed_vehicle_ids={duplicate_id}" in errors[-1].getMessage()
    assert "still_blocked=0 failed=1" in errors[-1].getMessage()
