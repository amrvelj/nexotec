"""KAN-266 step 3 (vehicle): every vehicle POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the action once) and
answers the same; the same key with a different request is a 409 for the
key. The mechanism itself is pinned in tests/test_idempotent_route.py.
"""

import random
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.provider import MappingGap
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.provider import resolve_provider_code

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(tenant_id: uuid.UUID, group_id: uuid.UUID | None = None, *, role: AccessRole | None = None) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group_id or uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=role is None,
    )


def _admin() -> str:
    return _token(uuid.uuid4(), role=AccessRole.PLATFORM_ADMIN)


def _headers(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _setup(client) -> str:
    """A dealership and a manager token in it."""

    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_headers(_admin()))
    assert response.status_code == 201, response.text
    dealership = response.json()
    return _token(uuid.UUID(dealership["id"]), uuid.UUID(dealership["dealerGroupId"]))


def _vin() -> str:
    return "".join(random.choices("ABCDEFGHJKLMNPRSTUVWXYZ0123456789", k=17))


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict."""

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["idempotencyKey"] == key


def _twice(client, path: str, token: str, body: dict | None):
    key = str(uuid.uuid4())
    first = client.post(path, json=body, headers=_headers(token, key))
    second = client.post(path, json=body, headers=_headers(token, key))
    assert first.status_code in (200, 201), first.text
    assert second.status_code == first.status_code
    assert second.json() == first.json()
    return key, first.json()


def _vehicle_mdm(client, token: str) -> dict:
    response = client.post("/v1/vehicle-mdm", json={"vin": _vin()}, headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()["vehicle"]


# --- POST /v1/vehicle-mdm ------------------------------------------------------


def test_create_vehicle_mdm_twice_under_one_key_replays_the_first_answer(client, db_session):
    """The route is create-or-get by VIN, so a re-run would also make one
    vehicle; what the replay changes is the answer: `created` stays true."""

    token = _setup(client)
    body = {"vin": _vin()}

    _, first = _twice(client, "/v1/vehicle-mdm", token, body)

    assert first["created"] is True
    assert _count(db_session, VehicleMdm, VehicleMdm.vin == body["vin"]) == 1


def test_create_vehicle_mdm_with_a_reused_key_and_another_vin_is_a_409(client, db_session):
    token = _setup(client)
    key, _ = _twice(client, "/v1/vehicle-mdm", token, {"vin": _vin()})
    other = _vin()

    response = client.post("/v1/vehicle-mdm", json={"vin": other}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleMdm, VehicleMdm.vin == other) == 0


# --- odometer readings and accessories -----------------------------------------

_DETAIL_CREATES = [
    (
        "odometer-readings",
        VehicleOdometerReading,
        {"value": 42000, "readingDate": "2026-01-01", "source": "manual"},
        {"value": 43000, "readingDate": "2026-02-01", "source": "manual"},
    ),
    (
        "accessories",
        VehicleAccessory,
        {"accessoryType": "towbar", "validFrom": "2024-01-01"},
        {"accessoryType": "roof_box", "validFrom": "2024-01-01"},
    ),
]


def test_each_detail_create_twice_under_one_key_makes_one_row(client, db_session):
    token = _setup(client)
    for segment, model, body, _other in _DETAIL_CREATES:
        vehicle = _vehicle_mdm(client, token)

        _twice(client, f"/v1/vehicle-mdm/{vehicle['id']}/{segment}", token, body)

        assert _count(db_session, model, model.vehicle_id == uuid.UUID(vehicle["id"])) == 1, segment


def test_each_detail_create_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _setup(client)
    for segment, model, body, other in _DETAIL_CREATES:
        vehicle = _vehicle_mdm(client, token)
        path = f"/v1/vehicle-mdm/{vehicle['id']}/{segment}"
        key, _ = _twice(client, path, token, body)

        response = client.post(path, json=other, headers=_headers(token, key))

        _assert_key_conflict(response, key)
        assert _count(db_session, model, model.vehicle_id == uuid.UUID(vehicle["id"])) == 1, segment


# --- POST /v1/vehicle-mdm/{id}/allocate ------------------------------------------


def _customer(client, token: str) -> dict:
    body = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    response = client.post("/v1/customers", json=body, headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def _open_owners(db_session, vehicle_id: str) -> list[uuid.UUID]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(VehicleParty.customer_id).where(
                VehicleParty.vehicle_id == uuid.UUID(vehicle_id),
                VehicleParty.role == VehiclePartyRole.OWNER,
                VehicleParty.effective_to.is_(None),
            )
        )
    )


def test_a_retried_allocation_replays_instead_of_taking_the_vehicle_back(client, db_session):
    """Allocating the same customer and role again is a no-op, so a retry
    straight after the first would pass without the key. A retry after
    another customer has become the owner shows it: run again, it would
    close B's ownership and make A the owner once more."""

    token = _setup(client)
    first_owner = _customer(client, token)
    next_owner = _customer(client, token)
    vehicle = _vehicle_mdm(client, token)
    path = f"/v1/vehicle-mdm/{vehicle['id']}/allocate"
    key = str(uuid.uuid4())

    first = client.post(path, json={"customerId": first_owner["id"], "role": "owner"}, headers=_headers(token, key))
    assert first.status_code == 201, first.text
    taken = client.post(path, json={"customerId": next_owner["id"], "role": "owner"}, headers=_headers(token))
    assert taken.status_code == 201, taken.text
    retry = client.post(path, json={"customerId": first_owner["id"], "role": "owner"}, headers=_headers(token, key))

    assert retry.status_code == first.status_code
    assert retry.json() == first.json()
    assert _open_owners(db_session, vehicle["id"]) == [uuid.UUID(next_owner["id"])]


def test_allocate_with_a_reused_key_and_another_customer_is_a_409(client, db_session):
    token = _setup(client)
    first_owner = _customer(client, token)
    other = _customer(client, token)
    vehicle = _vehicle_mdm(client, token)
    path = f"/v1/vehicle-mdm/{vehicle['id']}/allocate"
    key, _ = _twice(client, path, token, {"customerId": first_owner["id"], "role": "owner"})

    response = client.post(path, json={"customerId": other["id"], "role": "owner"}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _open_owners(db_session, vehicle["id"]) == [uuid.UUID(first_owner["id"])]


# --- POST /v1/configurations and its copy -----------------------------------------


def _variant(db_session) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="Golf GTI", model_year_from=2021, vehicle_kind="passenger_car",
        fuel_type="petrol", ps=245, base_price=Decimal("42500.00"),
    )
    db_session.add(variant)
    db_session.commit()
    return variant


def _configuration_body(variant: ModelVariant, **overrides) -> dict:
    body = {
        "source": "provider",
        "mode": "build",
        "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    }
    body.update(overrides)
    return body


def test_create_configuration_twice_under_one_key_makes_one_configuration(client, db_session):
    token = _setup(client)
    variant = _variant(db_session)

    _twice(client, "/v1/configurations", token, _configuration_body(variant))

    assert _count(db_session, VehicleConfiguration) == 1


def test_create_configuration_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _setup(client)
    variant = _variant(db_session)
    key, _ = _twice(client, "/v1/configurations", token, _configuration_body(variant))

    response = client.post(
        "/v1/configurations", json=_configuration_body(variant, wheels="19 inch"), headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleConfiguration) == 1


def _configuration(client, token: str, variant: ModelVariant) -> dict:
    response = client.post("/v1/configurations", json=_configuration_body(variant), headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_copy_configuration_twice_under_one_key_makes_one_copy(client, db_session):
    token = _setup(client)
    source = _configuration(client, token, _variant(db_session))

    _twice(client, f"/v1/configurations/{source['id']}/copy", token, None)

    assert _count(db_session, VehicleConfiguration) == 2


def test_copy_with_a_reused_key_on_another_configuration_is_a_409(client, db_session):
    """The copy has no body: what makes it another request is its target."""

    token = _setup(client)
    variant = _variant(db_session)
    source = _configuration(client, token, variant)
    other = _configuration(client, token, variant)
    key, _ = _twice(client, f"/v1/configurations/{source['id']}/copy", token, None)

    response = client.post(f"/v1/configurations/{other['id']}/copy", headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleConfiguration) == 3


# --- legacy POST /v1/vehicles and its custody events ---------------------------------


def _legacy_body(**overrides) -> dict:
    body = {"vin": _vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    body.update(overrides)
    return body


def test_create_legacy_vehicle_twice_under_one_key_makes_one_vehicle(client, db_session):
    token = _setup(client)
    body = _legacy_body()

    _twice(client, "/v1/vehicles", token, body)

    assert _count(db_session, Vehicle, Vehicle.vin == body["vin"]) == 1


def test_create_legacy_vehicle_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _setup(client)
    key, _ = _twice(client, "/v1/vehicles", token, _legacy_body())
    other = _legacy_body()

    response = client.post("/v1/vehicles", json=other, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, Vehicle, Vehicle.vin == other["vin"]) == 0


def _legacy_vehicle(client, token: str) -> dict:
    response = client.post("/v1/vehicles", json=_legacy_body(), headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_record_custody_event_twice_under_one_key_makes_one_event(client, db_session):
    token = _setup(client)
    vehicle = _legacy_vehicle(client, token)
    before = _count(db_session, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id == uuid.UUID(vehicle["id"]))

    _twice(client, f"/v1/vehicles/{vehicle['id']}/custody-events", token, {"eventType": "repossessed"})

    after = _count(db_session, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id == uuid.UUID(vehicle["id"]))
    assert after == before + 1


def test_a_retried_sale_out_of_custody_replays_its_success_instead_of_a_403(client, db_session):
    """After the sale the dealer is no longer the custodian, so running the
    retry again would refuse it ("only the current custodian may record a
    sale"); the replay answers with the sale that went through."""

    token = _setup(client)
    vehicle = _legacy_vehicle(client, token)

    _, event = _twice(client, f"/v1/vehicles/{vehicle['id']}/custody-events", token, {"eventType": "sold"})

    assert event["eventType"] == "sold"


def test_custody_event_with_a_reused_key_and_another_event_is_a_409(client, db_session):
    token = _setup(client)
    vehicle = _legacy_vehicle(client, token)
    path = f"/v1/vehicles/{vehicle['id']}/custody-events"
    key, _ = _twice(client, path, token, {"eventType": "repossessed"})
    before = _count(db_session, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id == uuid.UUID(vehicle["id"]))

    response = client.post(path, json={"eventType": "acquired"}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleCustodyEvent, VehicleCustodyEvent.vehicle_id == uuid.UUID(vehicle["id"])) == before


# --- catalogue administration: brands and mapping gaps (platform_admin) --------------


def test_create_brand_twice_under_one_key_makes_one_brand(client, db_session):
    code = f"brand-{uuid.uuid4().hex[:6]}"

    _twice(client, "/v1/vehicle-mdm/brands", _admin(), {"code": code, "displayName": "Alfa Romeo"})

    assert _count(db_session, Brand, Brand.code == code) == 1


def test_create_brand_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _admin()
    key, _ = _twice(client, "/v1/vehicle-mdm/brands", token, {"code": f"b-{uuid.uuid4().hex[:6]}", "displayName": "One"})
    other = f"b-{uuid.uuid4().hex[:6]}"

    response = client.post(
        "/v1/vehicle-mdm/brands", json={"code": other, "displayName": "Two"}, headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)
    assert _count(db_session, Brand, Brand.code == other) == 0


def _open_gap(db_session) -> str:
    ref_list = ReferenceList(list_code="fuel_type")
    db_session.add(ref_list)
    db_session.flush()
    for code in ("petrol", "diesel", "hydrogen"):
        db_session.add(
            ReferenceValue(list_id=ref_list.id, value_code=code, label_de=code, label_fr=code, label_it=code, label_en=code)
        )
    resolve_provider_code(db_session, provider="auto_i_dat", vehicle_kind="01", code_group="fuel_type", provider_code="12")
    db_session.commit()
    return str(db_session.scalar(select(MappingGap.id).where(MappingGap.provider_code == "12")))


def test_resolve_mapping_gap_twice_under_one_key_answers_the_same(client, db_session):
    """Resolving a gap to the value it already has is a no-op, so this one
    holds without the key as well; the 409 below is what the key adds."""

    path = f"/v1/vehicle-mdm/mapping-gaps/{_open_gap(db_session)}/resolve"

    _, body = _twice(client, path, _admin(), {"canonicalListCode": "fuel_type", "canonicalValueCode": "hydrogen"})

    assert body["resolvedValueCode"] == "hydrogen"


def test_resolve_mapping_gap_with_a_reused_key_and_another_value_is_a_409(client, db_session):
    token = _admin()
    gap_id = _open_gap(db_session)
    path = f"/v1/vehicle-mdm/mapping-gaps/{gap_id}/resolve"
    key, _ = _twice(client, path, token, {"canonicalListCode": "fuel_type", "canonicalValueCode": "hydrogen"})

    response = client.post(
        path, json={"canonicalListCode": "fuel_type", "canonicalValueCode": "diesel"}, headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(MappingGap, uuid.UUID(gap_id)).resolved_value_code == "hydrogen"
