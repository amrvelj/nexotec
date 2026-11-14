"""KAN-535 (ADR-103 as amended; Anto, 2026-11-13, option A): a car first
registered before 1981 that carries a chassis number instead of an ISO 3779
VIN (KAN-353) is bought onto stock. The purchase booking promotes it under its
chassis number, read with the make and the first registration off its
configuration; its vehicle-mdm record is that number together with the make,
compared tolerantly (case, spaces, hyphens and accents ignored). Over HTTP, on
Postgres.
"""

import datetime as dt
import re
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.auth import AccessRole, create_access_token, decode_token_claims
from app.core.validators import _VIN_RE
from app.inventory.models.stock_item import ISO_VIN_PATTERN, StockItem
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleIdentifierKind, VehicleMdm
from app.vehicle.services.matching import match_vehicle
from app.vehicle.services.vehicle_mdm import chassis_make_key, get_vehicle_mdm_by_vin
from tests.configuration_test_support import stock_configuration_id
from tests.test_inventory_purchase_promotes import _auth, _events, _purchase, _token
from tests.token_support import dealer_group_of

CHASSIS = "1130101234567"  # a W113 «Pagode» chassis number: 13 digits, no ISO VIN
PRE_1981 = dt.date(1968, 5, 2)


def _tenant_of(token: str) -> uuid.UUID:
    return uuid.UUID(decode_token_claims(token)["tenant_id"])


def _manager(token: str) -> str:
    tenant_id = _tenant_of(token)
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )


def _oldtimer(
    client,
    db_session,
    token: str,
    *,
    vin: str | None = CHASSIS,
    make: str | None = "Mercedes-Benz",
    first_registration: dt.date | None = PRE_1981,
) -> dict:
    """A pipeline item with its manual configuration, which carries the
    chassis number, the make and the first registration as the configurator
    stored them (its own KAN-353 rule is not this test's subject)."""

    configuration_id = stock_configuration_id(db_session, token)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(configuration_id))
    configuration.vin = vin
    configuration.brand_display_name = make
    configuration.first_registration_date = first_registration
    db_session.commit()
    response = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Mercedes-Benz 280 SL", "condition": "used", "configurationId": configuration_id},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _records(db_session) -> list[VehicleMdm]:
    db_session.expire_all()
    return list(db_session.scalars(select(VehicleMdm).order_by(VehicleMdm.vehicle_number)))


def _refused_and_untouched(db_session, response, item: dict, details: dict) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == details
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert stored.lifecycle_status.value == "pipeline"
    assert (stored.vin, stored.vehicle_id, stored.purchase_price) == (None, None, None)
    assert _records(db_session) == []


# --- the booking ---------------------------------------------------------------


def test_a_pre_1981_car_is_bought_onto_stock_under_its_chassis_number(client, db_session):
    token = _token(client)
    item = _oldtimer(client, db_session, token)

    response = _purchase(client, token, item, vin=CHASSIS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["lifecycleStatus"], body["vin"], body["isInvoiceable"]) == ("in_stock", CHASSIS, True)
    [record] = _records(db_session)
    assert record.identifier_kind == VehicleIdentifierKind.CHASSIS_NUMBER
    assert (record.vin, record.chassis_make_key, record.first_registration_date) == (CHASSIS, "mercedesbenz", PRE_1981)
    assert body["vehicleId"] == str(record.id)
    events = _events(db_session, item["id"])
    assert "inventory.pipeline_vehicle.vin_assigned" in events
    assert "inventory.stock_item.purchased" in events


def test_the_chassis_number_typed_in_another_spelling_is_the_same_number(client, db_session):
    token = _token(client)
    item = _oldtimer(client, db_session, token)

    response = _purchase(client, token, item, vin=" 1130 1012 34567 ")

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == CHASSIS


def test_the_same_car_bought_by_another_betrieb_under_another_spelling_of_its_make_is_one_record(client, db_session):
    first, second = _token(client), _token(client)
    assert _purchase(client, first, _oldtimer(client, db_session, first), vin=CHASSIS).status_code == 200
    item = _oldtimer(client, db_session, second, make=" mercedes benz ")

    response = _purchase(client, second, item, vin=CHASSIS)

    assert response.status_code == 200, response.text
    [record] = _records(db_session)
    assert response.json()["vehicleId"] == str(record.id)


def test_two_makes_sharing_a_chassis_number_are_two_cars_even_in_one_betriebs_stock(client, db_session):
    """Short chassis numbers repeat across makes — the reason the record is
    the number together with the make (option A), and why Stock counts such a
    car by its record, not by its number (`uq_stock_item_tenant_id_chassis_vehicle_in_stock`)."""

    token = _token(client)
    mercedes = _purchase(client, token, _oldtimer(client, db_session, token), vin=CHASSIS)
    porsche = _purchase(client, token, _oldtimer(client, db_session, token, make="Porsche"), vin=CHASSIS)

    assert mercedes.status_code == 200, mercedes.text
    assert porsche.status_code == 200, porsche.text
    records = _records(db_session)
    assert [r.chassis_make_key for r in records] == ["mercedesbenz", "porsche"]
    assert mercedes.json()["vehicleId"] != porsche.json()["vehicleId"]


def test_the_same_oldtimer_twice_in_one_betriebs_stock_is_refused(client, db_session):
    token = _token(client)
    held = _purchase(client, token, _oldtimer(client, db_session, token), vin=CHASSIS).json()
    second = _oldtimer(client, db_session, token, make="MERCEDES–BENZ")  # an en dash: the same make

    response = _purchase(client, token, second, vin=CHASSIS)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_already_in_stock"
    assert response.json()["error"]["details"]["stockItemId"] == held["id"]
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(second["id"])).vehicle_id is None


def test_a_stornoed_oldtimer_is_booked_again_under_its_record(client, db_session):
    token = _token(client)
    bought = _purchase(client, token, _oldtimer(client, db_session, token), vin=CHASSIS).json()
    storno = client.post(
        f"/v1/inventory/stock-items/{bought['id']}/purchase/storno",
        json={"reason": "Falsch erfasst"},
        headers=_auth(token, **{"If-Match": str(bought["version"])}),
    )
    assert storno.status_code == 200, storno.text

    again = _purchase(client, token, storno.json())

    assert again.status_code == 200, again.text
    assert (again.json()["vin"], again.json()["vehicleId"]) == (CHASSIS, bought["vehicleId"])
    assert len(_records(db_session)) == 1


# --- what the booking refuses, before anything is written ---------------------


def test_a_chassis_number_without_a_first_registration_before_1981_is_refused(client, db_session):
    token = _token(client)
    item = _oldtimer(client, db_session, token, first_registration=dt.date(1981, 1, 1))

    response = _purchase(client, token, item, vin=CHASSIS)

    _refused_and_untouched(
        db_session, response, item, {"field": "vin", "reason": "chassis_number_requires_pre_1981_registration"}
    )


def test_a_chassis_number_without_a_make_is_refused(client, db_session):
    token = _token(client)
    item = _oldtimer(client, db_session, token, make=" - ")

    response = _purchase(client, token, item, vin=CHASSIS)

    _refused_and_untouched(db_session, response, item, {"field": "make", "reason": "chassis_number_requires_make"})


def test_a_chassis_number_its_configuration_does_not_carry_is_refused(client, db_session):
    """A configuration without a VIN (a factory order) has nothing that ties
    a chassis number to a pre-1981 car and its make."""

    token = _token(client)
    item = _oldtimer(client, db_session, token, vin=None)

    response = _purchase(client, token, item, vin=CHASSIS)

    _refused_and_untouched(
        db_session,
        response,
        item,
        {"field": "vin", "reason": "chassis_number_requires_configuration", "stockItemId": item["id"]},
    )


def test_another_chassis_number_than_the_configurations_is_refused(client, db_session):
    token = _token(client)
    item = _oldtimer(client, db_session, token)

    response = _purchase(client, token, item, vin="1130109999999")

    _refused_and_untouched(db_session, response, item, {"field": "vin", "reason": "configuration_vin_differs"})


def test_another_betriebs_oldtimer_is_not_found(client, db_session):
    owner, stranger = _token(client), _token(client)
    item = _oldtimer(client, db_session, owner)

    response = _purchase(client, stranger, item, vin=CHASSIS)

    assert response.status_code == 404, response.text
    assert _records(db_session) == []


# --- the record keeps its identity --------------------------------------------


def _bought(client, db_session) -> tuple[str, dict, VehicleMdm]:
    token = _token(client)
    item = _purchase(client, token, _oldtimer(client, db_session, token), vin=CHASSIS).json()
    [record] = _records(db_session)
    return token, item, record


def test_a_chassis_number_record_is_never_found_by_its_number_alone(client, db_session):
    token, _item, _record = _bought(client, db_session)
    other = _token(client)
    _purchase(client, other, _oldtimer(client, db_session, other, make="Porsche"), vin=CHASSIS)

    assert get_vehicle_mdm_by_vin(db_session, CHASSIS) is None
    assert match_vehicle(db_session, vin=CHASSIS).vehicle is None  # two records share it: never a guess
    lookup = client.get("/v1/vehicle-mdm/lookup", params={"vin": CHASSIS}, headers=_auth(_manager(token)))
    assert lookup.status_code == 404, lookup.text


def test_the_identity_patch_keeps_a_chassis_number_records_number_and_pre_1981_registration(client, db_session):
    token, _item, record = _bought(client, db_session)
    headers = _auth(_manager(token), **{"If-Match": str(record.version)})

    vin = client.patch(f"/v1/vehicle-mdm/{record.id}", json={"vin": "WDB1130421A000001"}, headers=headers)
    registered = client.patch(
        f"/v1/vehicle-mdm/{record.id}", json={"firstRegistrationDate": "1985-03-01"}, headers=headers
    )
    stammnummer = client.patch(f"/v1/vehicle-mdm/{record.id}", json={"stammnummer": "123456789"}, headers=headers)

    assert vin.status_code == 422, vin.text
    assert vin.json()["error"]["details"] == {"field": "vin", "reason": "chassis_number_identity_fixed"}
    assert registered.status_code == 422, registered.text
    assert registered.json()["error"]["details"] == {
        "field": "firstRegistrationDate",
        "reason": "chassis_number_requires_pre_1981_registration",
    }
    assert stammnummer.status_code == 200, stammnummer.text
    db_session.expire_all()
    stored = db_session.get(VehicleMdm, record.id)
    assert (stored.vin, stored.first_registration_date, stored.stammnummer) == (CHASSIS, PRE_1981, "123456789")


def test_vin_korrigieren_leaves_a_chassis_number_record_alone(client, db_session):
    token, item, record = _bought(client, db_session)

    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/vin-correction",
        json={"newVin": "WDB1130421A000001", "reason": "Tippfehler"},
        headers=_auth(_manager(token), **{"If-Match": str(item["version"])}),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "chassis_number_identity_fixed"
    db_session.expire_all()
    assert db_session.get(VehicleMdm, record.id).vin == CHASSIS
    assert db_session.get(StockItem, uuid.UUID(item["id"])).vin == CHASSIS


# --- the database holds the rule ----------------------------------------------


def _insert(db_session, **values) -> None:
    db_session.add(VehicleMdm(vehicle_number=f"F-9{uuid.uuid4().int % 10**5:05d}", **values))
    db_session.flush()


def test_the_database_refuses_a_chassis_number_record_after_1980_or_without_a_make(db_session):
    for values in (
        {"first_registration_date": dt.date(1990, 1, 1), "chassis_make_key": "porsche"},
        {"first_registration_date": None, "chassis_make_key": "porsche"},
        {"first_registration_date": PRE_1981, "chassis_make_key": None},
    ):
        with pytest.raises(IntegrityError):
            _insert(db_session, vin=CHASSIS, identifier_kind=VehicleIdentifierKind.CHASSIS_NUMBER, **values)
        db_session.rollback()
    with pytest.raises(IntegrityError):  # a VIN record carries no make key
        _insert(db_session, vin="WDB1130421A000001", chassis_make_key="mercedesbenz")
    db_session.rollback()


def test_the_database_refuses_a_second_record_for_one_number_and_make(db_session):
    chassis = {
        "vin": CHASSIS,
        "identifier_kind": VehicleIdentifierKind.CHASSIS_NUMBER,
        "first_registration_date": PRE_1981,
    }
    _insert(db_session, chassis_make_key="porsche", **chassis)
    _insert(db_session, chassis_make_key="mercedesbenz", **chassis)
    with pytest.raises(IntegrityError):
        _insert(db_session, chassis_make_key="porsche", **chassis)
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 0


# --- the make key and the stock index pattern ---------------------------------


@pytest.mark.parametrize(
    ("make", "key"),
    [
        ("Mercedes-Benz", "mercedesbenz"),
        ("mercedes benz", "mercedesbenz"),
        ("MERCEDES – BENZ", "mercedesbenz"),
        ("Citroën", "citroen"),
        ("Škoda", "skoda"),
        ("Mercedes", "mercedes"),
        ("  -  ", None),
        ("", None),
        (None, None),
    ],
)
def test_a_make_is_compared_without_case_spaces_hyphens_or_accents(make, key):
    assert chassis_make_key(make) == key


def test_the_stock_vin_index_pattern_is_the_vin_validators():
    """The stock VIN index counts what `NormalisedVin` accepts; a chassis
    number (anything else) counts by its record."""

    assert ISO_VIN_PATTERN == _VIN_RE.pattern
    assert re.fullmatch(ISO_VIN_PATTERN, CHASSIS) is None
