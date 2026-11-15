"""KAN-730 (Anto, 2026-11-15, option 1; ADR-160 as amended): a merge that
kept the wrong VIN. The survivor carries the mistyped VIN, its merged
duplicate the right one, and the unique index holds the right one on the dead
record — so before this ticket «VIN korrigieren» to it was 409
`vin_names_merged_vehicle` naming the car itself, with no way out in the
product.

Now the correction takes the VIN over: the survivor and its own merged
duplicate swap VINs in Vehicle's one transaction (both audited, both
published), so the dead record still carries a VIN and a lookup by the
mistyped one still names the survivor. Another car's duplicate stays refused
(`test_kan729_vin_correction_to_a_merged_vin.py`). On Postgres.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem
from app.inventory.services import stock_item as stock_item_service
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import correct_vehicle_vin, resolve_vehicle_mdm_by_vin
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.test_inventory_purchase_promotes import VIN
from tests.test_inventory_vin_correction import REASON, _audit, _bought, _correct, _manager, _state

RIGHT_VIN = "WVWZZZ1KZAW730101"
OTHER_VIN = "WVWZZZ1KZAW730102"


def _merged_duplicate(db_session, *, survivor_id: uuid.UUID, vin: str = RIGHT_VIN) -> VehicleMdm:
    duplicate = create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor_id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    db_session.expire_all()
    return db_session.get(VehicleMdm, duplicate.id)


def _vin_events(db_session, vehicle_id: uuid.UUID) -> list[dict]:
    db_session.expire_all()
    rows = db_session.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.event_type == "vehicle.vehicle_mdm.vin_corrected")
        .where(OutboxMessage.aggregate_id == vehicle_id)
    )
    return [row.payload for row in rows]


def test_the_car_takes_the_right_vin_over_from_its_own_merged_duplicate(client, db_session):
    item, tenant_id = _bought(client, db_session)
    car_id = uuid.UUID(item["vehicleId"])
    duplicate = _merged_duplicate(db_session, survivor_id=car_id)
    duplicate_version = duplicate.version

    response = _correct(client, _manager(tenant_id), item, new_vin=RIGHT_VIN)

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == RIGHT_VIN
    assert _state(db_session, item) == (RIGHT_VIN, RIGHT_VIN, RIGHT_VIN)
    dead = db_session.get(VehicleMdm, duplicate.id)
    assert (dead.vin, dead.merged_into_vehicle_id) == (VIN, car_id)
    assert dead.version == duplicate_version + 1
    rows = {(row.entity_type, row.entity_id): row for row in _audit(db_session, "vin_corrected")}
    assert rows[("vehicle_mdm", car_id)].before == {"vin": VIN}
    assert rows[("vehicle_mdm", duplicate.id)].before == {"vin": RIGHT_VIN}
    assert rows[("vehicle_mdm", duplicate.id)].after == {"vin": VIN, "vinTakenOverBy": str(car_id)}
    assert rows[("vehicle_mdm", duplicate.id)].reason == REASON
    [published] = _vin_events(db_session, duplicate.id)
    assert (published["previousVin"], published["vin"], published["compensation"]) == (RIGHT_VIN, VIN, False)


def test_the_mistyped_vin_now_names_the_car_through_its_dead_record(client, db_session):
    """KAN-723 holds after the swap: the old VIN lands on the merged record and
    names the survivor, now under its right VIN."""

    item, tenant_id = _bought(client, db_session)
    car_id = uuid.UUID(item["vehicleId"])
    _merged_duplicate(db_session, survivor_id=car_id)
    assert _correct(client, _manager(tenant_id), item, new_vin=RIGHT_VIN).status_code == 200

    with pytest.raises(ConflictError) as refused:
        resolve_vehicle_mdm_by_vin(db_session, vin=VIN, actor_id=None)

    assert refused.value.details["reason"] == "vin_names_merged_vehicle"
    assert refused.value.details["vehicleId"] == str(car_id)
    assert refused.value.details["vin"] == RIGHT_VIN
    assert resolve_vehicle_mdm_by_vin(db_session, vin=RIGHT_VIN, actor_id=None).id == car_id


def test_a_failed_stock_write_swaps_the_two_back(client, db_session, monkeypatch):
    item, tenant_id = _bought(client, db_session)
    duplicate = _merged_duplicate(db_session, survivor_id=uuid.UUID(item["vehicleId"]))

    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)
    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item, new_vin=RIGHT_VIN)

    assert _state(db_session, item) == (VIN, VIN, VIN)
    assert db_session.get(VehicleMdm, duplicate.id).vin == RIGHT_VIN
    reverted = _audit(db_session, "vin_correction_reverted")
    assert sorted(
        (row.entity_id == duplicate.id, row.before["vin"], row.after["vin"])
        for row in reverted
        if row.entity_type == "vehicle_mdm"
    ) == [(False, RIGHT_VIN, VIN), (True, VIN, RIGHT_VIN)]


def test_a_duplicate_at_the_far_end_of_a_merge_chain_gives_its_vin_up(db_session):
    """A record merged into one that was merged into the car is the car's own too."""

    car = create_vehicle_mdm(db_session, vin=VIN, catalogue_variant_id=None)
    middle = create_vehicle_mdm(db_session, vin=OTHER_VIN, catalogue_variant_id=None)
    db_session.commit()
    first = _merged_duplicate(db_session, survivor_id=middle.id)
    merge_vehicles(db_session, survivor_id=car.id, duplicate_id=middle.id, actor_id=uuid.uuid4())

    corrected = correct_vehicle_vin(
        db_session,
        vehicle_id=car.id,
        old_vin=VIN,
        new_vin=RIGHT_VIN,
        reason=REASON,
        actor_id=None,
        tenant_id=uuid.uuid4(),
    )

    assert (corrected.vin, corrected.record_changed) == (RIGHT_VIN, True)
    db_session.expire_all()
    assert db_session.get(VehicleMdm, first.id).vin == VIN
    assert db_session.get(VehicleMdm, middle.id).vin == OTHER_VIN


def test_a_retry_after_the_take_over_writes_nothing_again(db_session):
    car = create_vehicle_mdm(db_session, vin=VIN, catalogue_variant_id=None)
    db_session.commit()
    duplicate = _merged_duplicate(db_session, survivor_id=car.id)
    tenant_id = uuid.uuid4()

    def _call():
        return correct_vehicle_vin(
            db_session,
            vehicle_id=car.id,
            old_vin=VIN,
            new_vin=RIGHT_VIN,
            reason=REASON,
            actor_id=None,
            tenant_id=tenant_id,
        )

    _call()
    again = _call()

    assert again.record_changed is False
    assert len(_vin_events(db_session, duplicate.id)) == 1
    assert db_session.get(VehicleMdm, duplicate.id).vin == VIN


def test_the_stock_car_s_configuration_and_item_follow(client, db_session):
    item, tenant_id = _bought(client, db_session)
    _merged_duplicate(db_session, survivor_id=uuid.UUID(item["vehicleId"]))

    assert _correct(client, _manager(tenant_id), item, new_vin=RIGHT_VIN).status_code == 200

    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    configuration = db_session.get(VehicleConfiguration, stored.configuration_id)
    assert (stored.vin, configuration.vin, configuration.vehicle_id) == (RIGHT_VIN, RIGHT_VIN, stored.vehicle_id)
