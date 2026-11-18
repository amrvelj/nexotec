"""KAN-729 (ADR-160, KAN-723 follow-up): «VIN korrigieren» to a VIN that only
a merged duplicate still carries. The duplicate keeps its (usually mistyped)
VIN after an FR-V-12 merge, so the lookup for the record carrying the new VIN
lands on a dead record. Before this ticket the correction was refused with
409 `vin_belongs_to_other_vehicle`, whose text sends the manager to merge two
records that are already merged.

It is now refused as every other write naming a merged record is (KAN-723):
409 `vin_names_merged_vehicle`, naming the record the car lives on and the VIN
it carries, with nothing written. The very car's own merged duplicate's VIN is
no longer refused: KAN-730 (Anto, 2026-11-15, option 1) takes it over —
`tests/vehicle/test_kan730_vin_taken_over_from_own_merged_duplicate.py`. On Postgres.
"""

import uuid

import pytest

from app.core.errors import ConflictError
from app.inventory.models.stock_item import StockItem
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import correct_vehicle_vin
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.inventory.test_inventory_purchase_promotes import VIN
from tests.inventory.test_inventory_vin_correction import REASON, _bought, _correct, _manager, _nothing_written

SURVIVOR_VIN = "WVWZZZ1KZAW729100"
DUPLICATE_VIN = "WVWZZZ1KZAW729101"


def _merge(db_session, *, survivor_id: uuid.UUID, duplicate_vin: str) -> VehicleMdm:
    duplicate = create_vehicle_mdm(db_session, vin=duplicate_vin, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor_id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    db_session.expire_all()
    return db_session.get(VehicleMdm, survivor_id)


def test_a_correction_to_another_cars_merged_duplicate_vin_names_the_live_record(client, db_session):
    item, tenant_id = _bought(client, db_session)
    other = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    survivor = _merge(db_session, survivor_id=other.id, duplicate_vin=DUPLICATE_VIN)

    response = _correct(client, _manager(tenant_id), item, new_vin=DUPLICATE_VIN)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {
        "reason": "vin_names_merged_vehicle",
        "vin": SURVIVOR_VIN,
        "vehicleNumber": survivor.vehicle_number,
        "vehicleId": str(survivor.id),
    }
    _nothing_written(db_session, item)


def test_vehicle_refuses_the_merged_vin_on_its_own_call(db_session):
    car = create_vehicle_mdm(db_session, vin=VIN, catalogue_variant_id=None)
    other = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    survivor = _merge(db_session, survivor_id=other.id, duplicate_vin=DUPLICATE_VIN)

    with pytest.raises(ConflictError) as refused:
        correct_vehicle_vin(
            db_session, vehicle_id=car.id, old_vin=VIN, new_vin=DUPLICATE_VIN, reason=REASON,
            actor_id=None, tenant_id=uuid.uuid4(),
        )

    assert refused.value.details["reason"] == "vin_names_merged_vehicle"
    assert refused.value.details["vehicleNumber"] == survivor.vehicle_number
    db_session.expire_all()
    assert db_session.get(VehicleMdm, car.id).vin == VIN


def test_a_vin_a_live_record_carries_is_still_vin_belongs_to_other_vehicle(client, db_session):
    item, tenant_id = _bought(client, db_session)
    other = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    db_session.commit()
    _merge(db_session, survivor_id=other.id, duplicate_vin=DUPLICATE_VIN)

    response = _correct(client, _manager(tenant_id), item, new_vin=SURVIVOR_VIN)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_belongs_to_other_vehicle"
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).vin == VIN
