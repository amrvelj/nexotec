"""KAN-535 follow-up (Anto, 2026-11-14, option B): a bought oldtimer's chassis
number is corrected through «VIN korrigieren», under the rules of a VIN
correction — the dealer manager only (`stock_vin_correction`), the stock
item, its vehicle-mdm record and its configuration together, each audited
with the reason, and the refusals a VIN gets. The record stays a
chassis-number record with its make and its pre-1981 first registration; a
corrected value that is a real 17-character VIN turns it into a VIN record.
The vehicle-mdm identity PATCH still refuses to change the number. Over HTTP,
on Postgres.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem
from app.inventory.services import stock_item as stock_item_service
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
from app.vehicle.public import resolve_vehicle_mdm_by_vin
from app.vehicle.services.vehicle_mdm import get_vehicle_mdm_by_vin
from tests.test_inventory_purchase_promotes import _auth, _purchase, _token
from tests.test_kan535_chassis_number_purchase import CHASSIS, PRE_1981, _manager, _oldtimer, _tenant_of
from tests.token_support import dealer_group_of

NEW_CHASSIS = "1130101234568"  # one digit further: the typo the correction repairs
REAL_VIN = "WDB1130421A000001"  # the same car's ISO 3779 VIN, found on its plate
REASON = "Fahrgestellnummer beim Einkauf falsch abgeschrieben."


def _bought(client, db_session, token: str | None = None, *, number: str = CHASSIS, make: str = "Mercedes-Benz"):
    token = token or _token(client)
    item = _oldtimer(client, db_session, token, vin=number, make=make)
    response = _purchase(client, token, item, vin=number)
    assert response.status_code == 200, response.text
    return token, response.json()


def _correct(client, token: str, item: dict, new_vin: str):
    return client.post(
        f"/v1/inventory/stock-items/{item['id']}/vin-correction",
        json={"newVin": new_vin, "reason": REASON},
        headers=_auth(token, **{"If-Match": str(item["version"])}),
    )


def _state(db_session, item: dict) -> tuple[str | None, str, str | None]:
    """(stock item, vehicle-mdm, configuration) numbers as stored."""

    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    vehicle = db_session.get(VehicleMdm, stored.vehicle_id)
    configuration = db_session.get(VehicleConfiguration, stored.configuration_id)
    return stored.vin, vehicle.vin, configuration.vin


def _record(db_session, item: dict) -> VehicleMdm:
    db_session.expire_all()
    return db_session.get(VehicleMdm, uuid.UUID(item["vehicleId"]))


def _audit(db_session, action: str) -> list[AuditEvent]:
    db_session.expire_all()
    return list(db_session.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)))


def _events(db_session, event_type: str) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == event_type)))


def _nothing_written(db_session, item: dict) -> None:
    assert _state(db_session, item) == (CHASSIS, CHASSIS, CHASSIS)
    assert _audit(db_session, "vin_corrected") == []
    assert _events(db_session, "inventory.stock_item.vin_corrected") == []
    assert _events(db_session, "vehicle.vehicle_mdm.vin_corrected") == []
    record = _record(db_session, item)
    assert (record.identifier_kind, record.chassis_make_key) == (VehicleIdentifierKind.CHASSIS_NUMBER, "mercedesbenz")
    assert db_session.get(StockItem, uuid.UUID(item["id"])).version == item["version"]


# --- the correction --------------------------------------------------------------


def test_a_manager_corrects_a_stocked_oldtimers_chassis_number_on_all_three_records(client, db_session):
    token, item = _bought(client, db_session)

    response = _correct(client, _manager(token), item, NEW_CHASSIS.lower())  # one spelling: upper-cased

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == NEW_CHASSIS
    assert _state(db_session, item) == (NEW_CHASSIS, NEW_CHASSIS, NEW_CHASSIS)
    record = _record(db_session, item)
    # Still the same chassis-number record: its make and its pre-1981 first registration stay.
    assert (record.identifier_kind, record.chassis_make_key, record.first_registration_date) == (
        VehicleIdentifierKind.CHASSIS_NUMBER,
        "mercedesbenz",
        PRE_1981,
    )
    rows = _audit(db_session, "vin_corrected")
    assert sorted(row.entity_type for row in rows) == ["stock_item", "vehicle_configuration", "vehicle_mdm"]
    assert {row.reason for row in rows} == {REASON}
    assert all(row.before["vin"] == CHASSIS and row.after["vin"] == NEW_CHASSIS for row in rows)
    [vehicle_event] = _events(db_session, "vehicle.vehicle_mdm.vin_corrected")
    assert (vehicle_event.payload["previousVin"], vehicle_event.payload["vin"]) == (CHASSIS, NEW_CHASSIS)
    [stock_event] = _events(db_session, "inventory.stock_item.vin_corrected")
    assert (stock_event.payload["previousVin"], stock_event.payload["vin"]) == (CHASSIS, NEW_CHASSIS)


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY])
def test_only_the_dealer_manager_corrects_a_chassis_number(client, db_session, role):
    token, item = _bought(client, db_session)
    tenant_id = _tenant_of(token)
    other = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)), roles=frozenset({role})
    )

    assert _correct(client, other, item, NEW_CHASSIS).status_code == 403
    _nothing_written(db_session, item)


def test_another_make_sharing_the_new_number_is_another_car(client, db_session):
    token, item = _bought(client, db_session)
    _bought(client, db_session, token, number=NEW_CHASSIS, make="Porsche")

    response = _correct(client, _manager(token), item, NEW_CHASSIS)

    assert response.status_code == 200, response.text
    assert _state(db_session, item) == (NEW_CHASSIS, NEW_CHASSIS, NEW_CHASSIS)


# --- the refusals a VIN gets -----------------------------------------------------


def test_a_number_another_record_of_the_make_carries_is_refused_like_a_vin(client, db_session):
    token, item = _bought(client, db_session)
    _bought(client, db_session, number=NEW_CHASSIS, make="mercedes benz")

    response = _correct(client, _manager(token), item, NEW_CHASSIS)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vin_belongs_to_other_vehicle"
    [other] = [r for r in db_session.scalars(select(VehicleMdm)) if r.vin == NEW_CHASSIS]
    assert details["vehicleNumber"] == other.vehicle_number
    _nothing_written(db_session, item)


def test_a_number_another_car_in_the_betriebs_stock_carries_is_refused_like_a_vin(client, db_session):
    token, item = _bought(client, db_session)
    _same, holder = _bought(client, db_session, token, number=NEW_CHASSIS, make="MERCEDES-BENZ")

    response = _correct(client, _manager(token), item, NEW_CHASSIS)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert (details["reason"], details["stockNumber"]) == ("vin_already_in_stock", holder["stockNumber"])
    _nothing_written(db_session, item)


def test_the_unchanged_number_is_refused(client, db_session):
    token, item = _bought(client, db_session)

    response = _correct(client, _manager(token), item, CHASSIS)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_unchanged"
    _nothing_written(db_session, item)


def test_a_value_that_is_no_chassis_number_is_refused(client, db_session):
    token, item = _bought(client, db_session)

    response = _correct(client, _manager(token), item, "113-010")

    assert response.status_code == 422, response.text
    _nothing_written(db_session, item)


def test_a_configuration_registered_after_1980_takes_no_chassis_number(client, db_session):
    token, item = _bought(client, db_session)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.first_registration_date = PRE_1981.replace(year=1985)
    db_session.commit()

    response = _correct(client, _manager(token), item, NEW_CHASSIS)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {
        "field": "newVin",
        "reason": "chassis_number_requires_pre_1981_registration",
    }
    _nothing_written(db_session, item)


def test_a_vin_record_takes_no_chassis_number(client, db_session):
    from tests.test_inventory_purchase_promotes import VIN, _create

    token = _token(client)
    item = _purchase(client, token, _create(client, db_session, token), vin=VIN).json()

    response = _correct(client, _manager(token), item, NEW_CHASSIS)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "newVin", "reason": "invalid_vin"}
    db_session.expire_all()
    assert db_session.get(VehicleMdm, uuid.UUID(item["vehicleId"])).vin == VIN
    assert _audit(db_session, "vin_corrected") == []


def test_the_identity_patch_still_refuses_to_change_the_number(client, db_session):
    token, item = _bought(client, db_session)
    record = _record(db_session, item)

    response = client.patch(
        f"/v1/vehicle-mdm/{record.id}",
        json={"vin": REAL_VIN},
        headers=_auth(_manager(token), **{"If-Match": str(record.version)}),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "vin", "reason": "chassis_number_identity_fixed"}
    _nothing_written(db_session, item)


# --- corrected to a real VIN: a VIN record from then on ---------------------------


def test_a_chassis_number_corrected_to_a_real_vin_makes_it_a_vin_record(client, db_session):
    token, item = _bought(client, db_session)

    response = _correct(client, _manager(token), item, REAL_VIN)

    assert response.status_code == 200, response.text
    assert _state(db_session, item) == (REAL_VIN, REAL_VIN, REAL_VIN)
    record = _record(db_session, item)
    assert (record.identifier_kind, record.chassis_make_key, record.first_registration_date) == (
        VehicleIdentifierKind.VIN,
        None,
        PRE_1981,
    )
    assert get_vehicle_mdm_by_vin(db_session, REAL_VIN).id == record.id
    [row] = [r for r in _audit(db_session, "vin_corrected") if r.entity_type == "vehicle_mdm"]
    assert row.before == {"vin": CHASSIS, "identifierKind": "chassis_number", "chassisMakeKey": "mercedesbenz"}
    assert row.after == {"vin": REAL_VIN, "identifierKind": "vin", "chassisMakeKey": None}


def test_a_real_vin_another_record_carries_is_refused(client, db_session):
    token, item = _bought(client, db_session)
    other = resolve_vehicle_mdm_by_vin(db_session, vin=REAL_VIN, actor_id=None)

    response = _correct(client, _manager(token), item, REAL_VIN)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vin_belongs_to_other_vehicle"
    assert details["vehicleNumber"] == db_session.get(VehicleMdm, other.id).vehicle_number
    _nothing_written(db_session, item)


# --- ADR-047: the compensation ---------------------------------------------------


def _fail_the_stock_write(monkeypatch) -> None:
    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)


@pytest.mark.parametrize("new_vin", [NEW_CHASSIS, REAL_VIN])
def test_a_failed_stock_write_puts_the_chassis_number_record_back(client, db_session, monkeypatch, new_vin):
    token, item = _bought(client, db_session)
    _fail_the_stock_write(monkeypatch)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(token), item, new_vin)

    assert _state(db_session, item) == (CHASSIS, CHASSIS, CHASSIS)
    record = _record(db_session, item)
    assert (record.identifier_kind, record.chassis_make_key) == (VehicleIdentifierKind.CHASSIS_NUMBER, "mercedesbenz")
    reverted = _audit(db_session, "vin_correction_reverted")
    assert sorted((row.entity_type, row.before["vin"], row.after["vin"]) for row in reverted) == [
        ("vehicle_configuration", new_vin, CHASSIS),
        ("vehicle_mdm", new_vin, CHASSIS),
    ]
