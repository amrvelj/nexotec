"""KAN-723 (ADR-160, KAN-524 amendment): after an FR-V-12 merge the duplicate
keeps its (usually mistyped) VIN, so a lookup by that VIN still lands on the
merged record. A write that links a car by such a lookup must never link the
dead record:

- Stock's purchase booking (`vehicle.public.resolve_vehicle_mdm_by_vin`) is
  refused, 409 `vin_names_merged_vehicle`, naming the record the car lives on
  and its VIN — nothing created, the item stays in pipeline.
- A configuration PATCH (and create) carrying that VIN links the survivor.
- A trade-in or valuation match by that VIN answers the survivor.

On Postgres.
"""

import uuid

import pytest

from app.core.errors import ConflictError
from app.inventory.models.stock_item import StockItem
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import match_vehicle, resolve_vehicle_mdm_by_vin
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.test_configuration_field_rules import _manual, _patch, _seed_coded_lists
from tests.test_inventory_purchase_promotes import _create, _purchase, _token

SURVIVOR_VIN = "WVWZZZ1KZAW723100"
DUPLICATE_VIN = "WVWZZZ1KZAW723101"
UNSEEN_VIN = "WVWZZZ1KZAW723102"


def _merged_pair(db_session) -> tuple[VehicleMdm, VehicleMdm]:
    survivor = create_vehicle_mdm(db_session, vin=SURVIVOR_VIN, catalogue_variant_id=None)
    duplicate = create_vehicle_mdm(db_session, vin=DUPLICATE_VIN, catalogue_variant_id=None)
    db_session.commit()
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    db_session.expire_all()
    return db_session.get(VehicleMdm, survivor.id), db_session.get(VehicleMdm, duplicate.id)


def _record_count(db_session) -> int:
    db_session.expire_all()
    return len(db_session.query(VehicleMdm).all())


def test_a_purchase_booking_with_the_duplicates_vin_is_refused_and_names_the_live_record(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)
    survivor, _duplicate = _merged_pair(db_session)
    records_before = _record_count(db_session)

    response = _purchase(client, token, item, vin=DUPLICATE_VIN)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {
        "reason": "vin_names_merged_vehicle",
        "vin": SURVIVOR_VIN,
        "vehicleNumber": survivor.vehicle_number,
        "vehicleId": str(survivor.id),
    }
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert stored.lifecycle_status.value == "pipeline"
    assert stored.vehicle_id is None
    assert stored.purchase_price is None
    assert _record_count(db_session) == records_before


def test_a_purchase_booking_with_the_survivors_vin_still_links_the_survivor(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)
    survivor, _duplicate = _merged_pair(db_session)

    response = _purchase(client, token, item, vin=SURVIVOR_VIN)

    assert response.status_code == 200, response.text
    assert response.json()["vehicleId"] == str(survivor.id)


def test_the_vin_resolution_refuses_a_merged_record_and_still_creates_an_unseen_one(db_session):
    survivor, _duplicate = _merged_pair(db_session)

    with pytest.raises(ConflictError) as refused:
        resolve_vehicle_mdm_by_vin(db_session, vin=DUPLICATE_VIN, actor_id=None)
    assert refused.value.details["reason"] == "vin_names_merged_vehicle"
    assert refused.value.details["vin"] == survivor.vin

    created = resolve_vehicle_mdm_by_vin(db_session, vin=UNSEEN_VIN, actor_id=None)
    assert created.vin == UNSEEN_VIN


def test_a_configuration_patched_to_the_duplicates_vin_links_the_live_record(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    survivor, duplicate = _merged_pair(db_session)
    created = _manual(client, tenant_id)

    response = _patch(client, tenant_id, created["id"], created["version"], {"vin": DUPLICATE_VIN})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["vin"] == DUPLICATE_VIN  # the configuration keeps the VIN as typed
    assert (body["vehicleId"], body["vehicleLabel"]) == (str(survivor.id), survivor.vehicle_number)
    assert body["vehicleId"] != str(duplicate.id)


def test_a_configuration_created_with_the_duplicates_vin_links_the_live_record(client, db_session):
    _seed_coded_lists(db_session)
    survivor, _duplicate = _merged_pair(db_session)

    created = _manual(client, uuid.uuid4(), vin=DUPLICATE_VIN)

    assert created["vehicleId"] == str(survivor.id)


def test_a_match_by_the_duplicates_vin_answers_the_live_record(db_session):
    survivor, _duplicate = _merged_pair(db_session)

    match = match_vehicle(db_session, vin=DUPLICATE_VIN, group_id=uuid.uuid4())

    assert match.vehicle is not None and match.vehicle.id == survivor.id
