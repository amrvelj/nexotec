"""WP-5 PR-9: Vehicle 360 detail endpoints — plates/odometer/accessories/
party-roles, each scoped by a known vehicle id.
"""

import uuid

from app.core.auth import create_access_token

VALID_VIN = "1HGCM82633A004352"


def _token(is_dealer_manager: bool = True) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_vehicle(client, token) -> dict:
    return client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()["vehicle"]


def test_odometer_reading_round_trip(client):
    token = _token()
    vehicle = _create_vehicle(client, token)

    create = client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/odometer-readings",
        json={"value": 42000, "readingDate": "2026-01-01", "source": "manual"},
        headers=_bearer(token),
    )
    assert create.status_code == 201, create.text

    listed = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/odometer-readings", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["value"] == 42000


def test_decreasing_reading_is_flagged_and_still_listed(client):
    token = _token()
    vehicle = _create_vehicle(client, token)
    client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/odometer-readings",
        json={"value": 50000, "readingDate": "2026-06-01", "source": "manual"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/odometer-readings",
        json={"value": 40000, "readingDate": "2026-07-01", "source": "manual"},
        headers=_bearer(token),
    )

    readings = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/odometer-readings", headers=_bearer(token)).json()
    assert len(readings) == 2
    lower = next(r for r in readings if r["value"] == 40000)
    assert lower["implausible"] is True


def test_accessory_add_and_close_via_delete(client):
    token = _token()
    vehicle = _create_vehicle(client, token)

    created = client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/accessories",
        json={"accessoryType": "towbar", "validFrom": "2024-01-01"},
        headers=_bearer(token),
    ).json()

    close = client.delete(f"/v1/vehicle-mdm/{vehicle['id']}/accessories/{created['id']}", headers=_bearer(token))
    assert close.status_code == 204, close.text

    listed = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/accessories", headers=_bearer(token)).json()
    assert len(listed) == 1  # still present, never deleted
    assert listed[0]["validTo"] is not None


def test_party_roles_default_current_only(client, db_session):
    token = _token()
    vehicle = _create_vehicle(client, token)

    from app.customer.models.customer import Customer, CustomerType, Language
    from app.customer.models.vehicle_party import VehiclePartyRole
    from app.customer.services.customer import allocate_vehicle_party

    group_id = uuid.uuid4()
    alice = Customer(
        group_id=group_id, customer_number="K-100001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Alice", last_name="A",
    )
    bob = Customer(
        group_id=group_id, customer_number="K-100002", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Bob", last_name="B",
    )
    db_session.add_all([alice, bob])
    db_session.flush()

    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=group_id, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=bob.id, role=VehiclePartyRole.OWNER,
        group_id=group_id, actor_id=uuid.uuid4(),
    )

    current = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/party-roles", headers=_bearer(token)).json()
    assert len(current) == 1
    assert current[0]["customerId"] == str(bob.id)

    history = client.get(
        f"/v1/vehicle-mdm/{vehicle['id']}/party-roles?include_closed=true", headers=_bearer(token)
    ).json()
    assert len(history) == 2
