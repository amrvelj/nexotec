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


def _token(role: AccessRole, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> str:
    return create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id or uuid.uuid4(), access_role=role
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
    response = client.post("/v1/dealers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "private", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    assert body["vehicle"]["make"] == "Honda"

    listed = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(token)).json()["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_create_rejects_effective_to_before_effective_from(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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


def test_create_duplicate_role_same_effective_from_conflicts(client):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"vehicleId": vehicle["id"], "role": "owner", "effectiveFrom": "2026-01-01T00:00:00Z"}
    first = client.post(f"/v1/customers/{customer['id']}/vehicles", json=payload, headers=_bearer(token))
    assert first.status_code == 201, first.text
    second = client.post(f"/v1/customers/{customer['id']}/vehicles", json=payload, headers=_bearer(token))
    assert second.status_code == 409, second.text


def test_create_with_nonexistent_vehicle_404s(client):
    dealer_id, customer, _vehicle = _setup(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    other_tenant_token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(other_dealer_id))
    response = client.get(f"/v1/customers/{customer['id']}/vehicles", headers=_bearer(other_tenant_token))
    assert response.status_code == 404, response.text
