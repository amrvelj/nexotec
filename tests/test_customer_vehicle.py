"""Customer<->Vehicle relationship endpoints (D-12, FR-10): owner/keeper/
driver roles with effective-from/to, backing the 360 view's Vehicles tab.
"""

import uuid

from app.core.auth import AccessRole, create_access_token

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=_tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _random_vin() -> str:
    import random

    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, **overrides) -> dict:
    """KAN-31: vehicle_mdm (WP-5's three-layer model), never the legacy
    `vehicle` table this replaces — that table's writes are frozen
    (ADR-021) in production, and the customer-vehicle-link endpoints under
    test resolve against vehicle_mdm now. No catalogue_variant_id by
    default (the unmatched case, and the common one — see
    VehiclePartySummary's own docstring), so the resulting
    VehiclePartySummary.make/model/trim are None; tests that need a
    catalogue match ask for one explicitly.
    """

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin()}
    payload.update(overrides)
    response = client.post("/v1/vehicle-mdm", json=payload, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()["vehicle"]


def _setup(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    return dealer_id, customer, vehicle


# --- create / list -----------------------------------------------------------------


def test_list_starts_empty(client):
    dealer_id, customer, _vehicle = _setup(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_create_assigns_role_and_embeds_vehicle_summary(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "owner"},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "owner"
    assert body["customerId"] == customer["id"]
    assert body["vehicleId"] == vehicle["id"]
    assert body["effectiveFrom"] is not None
    assert body["effectiveTo"] is None
    assert body["vehicle"]["vin"] == vehicle["vin"]
    assert body["vehicle"]["vehicleNumber"] == vehicle["vehicleNumber"]
    # No catalogue match (the default in this suite's fixtures) -> None,
    # never guessed at. See test_summary_resolves_make_model_trim_from_a_
    # matched_catalogue_variant below for the matched case.
    assert body["vehicle"]["make"] is None
    assert body["vehicle"]["model"] is None
    assert body["vehicle"]["modelYear"] is None

    listed = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token)).json()["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_create_rejects_effective_to_before_effective_from(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={
            "vehicleId": vehicle["id"],
            "role": "keeper",
            "effectiveFrom": "2026-06-01T00:00:00Z",
            "effectiveTo": "2026-01-01T00:00:00Z",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 400, response.text


def test_create_reconfirming_the_same_holder_is_idempotent_not_a_conflict(client):
    """KAN-31: this endpoint now delegates to allocate_vehicle_party
    (ADR-064) for the ordinary create path, same function the vehicle-side
    POST /vehicle-mdm/{id}/allocate calls. Its own documented semantics:
    re-confirming the SAME customer for a role they already hold is a
    no-op returning the existing open row — not a second row, and not a
    conflict (the old raw-insert + UniqueConstraint mechanism this test
    used to exercise made it a 409; that was an accident of the old
    implementation, not a contract this endpoint ever documented).
    """

    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"vehicleId": vehicle["id"], "role": "owner", "effectiveFrom": "2026-01-01T00:00:00Z"}
    first = client.post(f"/v1/customers/{customer['id']}/vehicles", json=payload, headers=_bearer(token))
    assert first.status_code == 201, first.text
    second = client.post(f"/v1/customers/{customer['id']}/vehicles", json=payload, headers=_bearer(token))
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]

    listed = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token)).json()["items"]
    assert len(listed) == 1  # never a second open row for the same (vehicle, role)


def test_create_a_different_customer_claiming_the_same_role_closes_the_first(client):
    """The ADR-064 property that actually matters: a DIFFERENT customer
    taking over a role someone else holds closes the incumbent, it never
    creates a second open row for the same (vehicle, role) — the seam
    KAN-31 exists to make the customer-side create path honour, same as
    the vehicle-side allocate endpoint already does.
    """

    dealer_id, first_customer, vehicle = _setup(client)
    second_customer = _create_customer(client, dealer_id, email=f"second-{uuid.uuid4().hex[:8]}@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    first = client.post(
        f"/v1/customers/{first_customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "owner"}, headers=_bearer(token),
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/v1/customers/{second_customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "owner"}, headers=_bearer(token),
    )
    assert second.status_code == 201, second.text

    # Closed, not deleted (this endpoint's default view excludes closed
    # rows; the service-level include_closed=True path is covered in
    # test_customer_vehicle_party_allocation.py).
    first_customer_open = client.get(
        f"/v1/customers/{first_customer['id']}/vehicles", headers=_bearer(token)
    ).json()["items"]
    assert first_customer_open == []


def test_create_with_nonexistent_vehicle_404s(client):
    dealer_id, customer, _vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": str(uuid.uuid4()), "role": "driver"},
        headers=_bearer(token),
    )
    assert response.status_code == 404, response.text


def test_multiple_roles_on_same_vehicle_coexist(client):
    """One vehicle can have several parties simultaneously in different
    roles (FR-10) — owner and driver for the same vehicle+customer at once.
    """

    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    owner = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "owner"},
        headers=_bearer(token),
    )
    driver = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "driver"},
        headers=_bearer(token),
    )
    assert owner.status_code == 201, owner.text
    assert driver.status_code == 201, driver.text
    listed = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token)).json()["items"]
    assert {row["role"] for row in listed} == {"owner", "driver"}


# --- update / delete ----------------------------------------------------------------


def test_update_sets_effective_to_ending_the_relationship(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "keeper"},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/customers/{customer['id']}/vehicles/{created['id']}",
        json={"effectiveTo": "2030-01-01T00:00:00Z"},
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["effectiveTo"] is not None


def test_update_rejects_effective_to_before_effective_from(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "keeper", "effectiveFrom": "2026-06-01T00:00:00Z"},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/customers/{customer['id']}/vehicles/{created['id']}",
        json={"effectiveTo": "2020-01-01T00:00:00Z"},
        headers=_bearer(token),
    )
    assert response.status_code == 400, response.text


def test_delete_removes_relationship(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "driver"},
        headers=_bearer(token),
    ).json()

    delete_response = client.delete(
        f"/v1/customers/{customer['id']}/vehicles/{created['id']}", headers=_bearer(token)
    )
    assert delete_response.status_code == 204, delete_response.text

    listed = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token)).json()["items"]
    assert listed == []


# --- access control / tenancy -------------------------------------------------------


def test_inventory_role_cannot_write(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(AccessRole.INVENTORY, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={"vehicleId": vehicle["id"], "role": "owner"},
        headers=_bearer(token),
    )
    assert response.status_code == 403, response.text


def test_customer_from_another_tenant_404s_not_403(client):
    _dealer_id, customer, _vehicle = _setup(client)
    other_dealer_id = _create_dealer(client)
    other_tenant_token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(other_dealer_id))
    response = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(other_tenant_token))
    assert response.status_code == 404, response.text


# --- crossing the seam (KAN-31) -----------------------------------------------------
#
# The two existing suites this ticket found used two different tables and
# neither crossed the seam: tests/test_customer_vehicle.py (this file, pre-fix)
# built its fixture via legacy POST /v1/vehicles; test_customer_vehicle_party_
# allocation.py used create_vehicle_mdm directly, at the service layer, never
# through this file's HTTP-level customer-side endpoint. This is the case that
# 500'd before the fix: allocate from the VEHICLE side, read from the CUSTOMER
# side, through the real HTTP endpoints both ways.


def test_allocating_from_the_vehicle_side_is_readable_from_the_customer_side(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    allocate = client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/allocate",
        json={"customerId": customer["id"], "role": "keeper"},
        headers=_bearer(token),
    )
    assert allocate.status_code == 201, allocate.text

    response = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token))
    assert response.status_code == 200, response.text  # not the 500 this ticket fixes
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["role"] == "keeper"
    assert items[0]["vehicle"]["vin"] == vehicle["vin"]


def test_a_second_owner_allocated_from_the_vehicle_side_closes_the_first_read_from_the_customer_side(client):
    """ADR-064's actual property, asserted across the seam: allocating a
    second owner from the vehicle side closes the first — the customer
    side must see the timeline, not just the current holder.
    """

    dealer_id, first_customer, vehicle = _setup(client)
    second_customer = _create_customer(client, dealer_id, email=f"second-{uuid.uuid4().hex[:8]}@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/allocate",
        json={"customerId": first_customer["id"], "role": "owner"}, headers=_bearer(token),
    )
    second_allocate = client.post(
        f"/v1/vehicle-mdm/{vehicle['id']}/allocate",
        json={"customerId": second_customer["id"], "role": "owner"}, headers=_bearer(token),
    )
    assert second_allocate.status_code == 201, second_allocate.text

    first_customer_vehicles = client.get(
        f"/v1/customers/{first_customer['id']}/vehicles", headers=_bearer(token)
    ).json()["items"]
    assert first_customer_vehicles == []  # closed, not silently left as current

    second_customer_vehicles = client.get(
        f"/v1/customers/{second_customer['id']}/vehicles", headers=_bearer(token)
    ).json()["items"]
    assert len(second_customer_vehicles) == 1
    assert second_customer_vehicles[0]["role"] == "owner"
