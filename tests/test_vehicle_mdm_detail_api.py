"""WP-5 PR-9: Vehicle 360 detail endpoints — plates/odometer/accessories/
party-roles, each scoped by a known vehicle id.
"""

import uuid

from app.core.auth import create_access_token

VALID_VIN = "1HGCM82633A004352"


def _token(is_dealer_manager: bool = True, group_id: uuid.UUID | None = None) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid,
        group_id=group_id if group_id is not None else uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
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
    group_id = uuid.uuid4()
    token = _token(group_id=group_id)
    vehicle = _create_vehicle(client, token)

    from app.customer.models.customer import Customer, CustomerType, Language
    from app.customer.models.vehicle_party import VehiclePartyRole
    from app.customer.services.customer import allocate_vehicle_party

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


def test_party_roles_never_leaks_another_groups_customer(client, db_session):
    """vehicle_mdm is a deliberately global fact (ADR-022): two entirely
    unrelated dealer groups can attach a VehicleParty row to the SAME
    vehicle_id, since the table carries no group_id of its own. Group A's
    principal must never see group B's customer's id (or role) as a party
    on a car neither dealership has any relationship over — rule #7
    (cross-tenant reads are silently scoped, never a leak) and ADR-049.
    """

    from app.customer.models.customer import Customer, CustomerType, Language
    from app.customer.models.vehicle_party import VehiclePartyRole
    from app.customer.services.customer import allocate_vehicle_party

    group_a = uuid.uuid4()
    group_b = uuid.uuid4()
    token_a = _token(group_id=group_a)
    vehicle = _create_vehicle(client, token_a)

    customer_a = Customer(
        group_id=group_a, customer_number="K-200001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Group-A", last_name="Owner",
    )
    customer_b = Customer(
        group_id=group_b, customer_number="K-200002", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Group-B", last_name="Driver",
    )
    db_session.add_all([customer_a, customer_b])
    db_session.flush()

    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=customer_a.id, role=VehiclePartyRole.OWNER,
        group_id=group_a, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=customer_b.id, role=VehiclePartyRole.DRIVER,
        group_id=group_b, actor_id=uuid.uuid4(),
    )

    seen_by_a = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/party-roles", headers=_bearer(token_a)).json()
    assert len(seen_by_a) == 1
    assert seen_by_a[0]["customerId"] == str(customer_a.id)
    seen_customer_ids = {row["customerId"] for row in seen_by_a}
    assert str(customer_b.id) not in seen_customer_ids

    token_b = _token(group_id=group_b)
    seen_by_b = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/party-roles", headers=_bearer(token_b)).json()
    assert len(seen_by_b) == 1
    assert seen_by_b[0]["customerId"] == str(customer_b.id)


def test_party_roles_carry_the_holders_display_name_current_and_closed(client, db_session):
    """KAN-140: the Identity tab shows who holds each role, not a raw
    customer UUID — so every row, current and closed, carries the
    customer's display name (same precedence as the customer side's
    "other parties": company name, else first+last, else number)."""

    group_id = uuid.uuid4()
    token = _token(group_id=group_id)
    vehicle = _create_vehicle(client, token)

    from app.customer.models.customer import Customer, CustomerType, Language
    from app.customer.models.vehicle_party import VehiclePartyRole
    from app.customer.services.customer import allocate_vehicle_party

    former = Customer(
        group_id=group_id, customer_number="K-300001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Frieda", last_name="Former",
    )
    company = Customer(
        group_id=group_id, customer_number="K-300002", customer_type=CustomerType.BUSINESS,
        language=Language.EN, company_name="Leasing AG",
    )
    db_session.add_all([former, company])
    db_session.flush()

    for holder in (former, company):
        allocate_vehicle_party(
            db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=holder.id, role=VehiclePartyRole.KEEPER,
            group_id=group_id, actor_id=uuid.uuid4(),
        )

    current = client.get(f"/v1/vehicle-mdm/{vehicle['id']}/party-roles", headers=_bearer(token)).json()
    assert [(row["customerId"], row["displayName"]) for row in current] == [(str(company.id), "Leasing AG")]

    history = client.get(
        f"/v1/vehicle-mdm/{vehicle['id']}/party-roles?include_closed=true", headers=_bearer(token)
    ).json()
    assert {row["customerId"]: row["displayName"] for row in history} == {
        str(company.id): "Leasing AG",
        str(former.id): "Frieda Former",
    }


def test_party_roles_never_resolve_another_groups_customer_name(client, db_session):
    """KAN-140's Do-not-touch: adding the name must not widen the group
    scope — group B's holder on the same VIN stays invisible to group A,
    name included, in the history view as well as the default one."""

    from app.customer.models.customer import Customer, CustomerType, Language
    from app.customer.models.vehicle_party import VehiclePartyRole
    from app.customer.services.customer import allocate_vehicle_party

    group_a = uuid.uuid4()
    group_b = uuid.uuid4()
    token_a = _token(group_id=group_a)
    vehicle = _create_vehicle(client, token_a)

    mine = Customer(
        group_id=group_a, customer_number="K-400001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Anna", last_name="Mine",
    )
    theirs = Customer(
        group_id=group_b, customer_number="K-400002", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Secret", last_name="Elsewhere",
    )
    db_session.add_all([mine, theirs])
    db_session.flush()

    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=mine.id, role=VehiclePartyRole.OWNER,
        group_id=group_a, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=uuid.UUID(vehicle["id"]), customer_id=theirs.id, role=VehiclePartyRole.OWNER,
        group_id=group_b, actor_id=uuid.uuid4(),
    )

    response = client.get(
        f"/v1/vehicle-mdm/{vehicle['id']}/party-roles?include_closed=true", headers=_bearer(token_a)
    )
    assert response.status_code == 200, response.text
    assert [row["displayName"] for row in response.json()] == ["Anna Mine"]
    assert "Elsewhere" not in response.text
