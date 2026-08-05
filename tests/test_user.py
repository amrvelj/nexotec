import uuid

import pytest

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


def _user_payload(**overrides):
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": "anna@example.ch",
        "role": "admin",
        "accessRole": "dealer_admin",
        "authIdentityId": "stub-sub-1",
    }
    payload.update(overrides)
    return payload


def _create_user(client, dealer_id: str, platform_admin_token: str, **overrides):
    response = client.post(
        f"/v1/dealers/{dealer_id}/users",
        json=_user_payload(**overrides),
        headers=_bearer(platform_admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- bootstrap flow: platform_admin creates Dealer + initial admin User -----


def test_platform_admin_bootstraps_dealer_and_initial_admin_user(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)

    user = _create_user(client, dealer_id, platform_admin_token)
    assert user["dealerId"] == dealer_id
    assert user["status"] == "invited"
    assert user["accessRole"] == "dealer_admin"


def test_dealer_admin_can_add_staff_within_own_tenant(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    admin_user = _create_user(client, dealer_id, platform_admin_token)

    dealer_admin_token = create_access_token(
        user_id=uuid.UUID(admin_user["id"]), tenant_id=uuid.UUID(dealer_id), access_role=AccessRole.DEALER_ADMIN
    )
    response = client.post(
        f"/v1/dealers/{dealer_id}/users",
        json=_user_payload(email="bob@example.ch", role="sales", accessRole="sales"),
        headers=_bearer(dealer_admin_token),
    )
    assert response.status_code == 201


def test_dealer_admin_cannot_add_staff_to_other_dealer(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    other_dealer_admin_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant_id

    response = client.post(
        f"/v1/dealers/{dealer_id}/users",
        json=_user_payload(email="intruder@example.ch"),
        headers=_bearer(other_dealer_admin_token),
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_admin_roles_cannot_create_users(client, role):
    dealer_id = _create_dealer(client)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/dealers/{dealer_id}/users", json=_user_payload(email="x@example.ch"), headers=_bearer(token)
    )
    assert response.status_code == 403


def test_creating_user_under_nonexistent_dealer_is_404(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post(
        f"/v1/dealers/{uuid.uuid4()}/users", json=_user_payload(), headers=_bearer(token)
    )
    assert response.status_code == 404


# --- duplicate email --------------------------------------------------------


def test_duplicate_email_is_rejected(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    _create_user(client, dealer_id, platform_admin_token, email="dup@example.ch")

    response = client.post(
        f"/v1/dealers/{dealer_id}/users",
        json=_user_payload(email="dup@example.ch"),
        headers=_bearer(platform_admin_token),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_duplicate_email_across_different_dealers_is_still_rejected(client):
    """Email is globally unique per spec, not unique-per-tenant."""
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_user(client, dealer_a, platform_admin_token, email="shared@example.ch")

    response = client.post(
        f"/v1/dealers/{dealer_b}/users",
        json=_user_payload(email="shared@example.ch"),
        headers=_bearer(platform_admin_token),
    )
    assert response.status_code == 409


# --- cross-tenant isolation ---------------------------------------------------


def test_get_user_cross_tenant_is_404(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, platform_admin_token)

    other_token = _token(AccessRole.DEALER_ADMIN)  # different random tenant_id
    response = client.get(f"/v1/dealers/{dealer_id}/users/{user['id']}", headers=_bearer(other_token))
    assert response.status_code == 404


# --- lifecycle: terminated employment is terminal + revokes access -----------


def test_terminating_employment_auto_deactivates_status(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, platform_admin_token)

    response = client.patch(
        f"/v1/dealers/{dealer_id}/users/{user['id']}",
        json={"employmentStatus": "terminated"},
        headers={**_bearer(platform_admin_token), "If-Match": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["employmentStatus"] == "terminated"
    assert body["status"] == "deactivated"


def test_terminated_employment_status_is_terminal(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, platform_admin_token)

    headers = _bearer(platform_admin_token)
    client.patch(
        f"/v1/dealers/{dealer_id}/users/{user['id']}",
        json={"employmentStatus": "terminated"},
        headers={**headers, "If-Match": "1"},
    )
    response = client.patch(
        f"/v1/dealers/{dealer_id}/users/{user['id']}",
        json={"employmentStatus": "active"},
        headers={**headers, "If-Match": "2"},
    )
    assert response.status_code == 409


# --- audit logging -----------------------------------------------------------


def test_role_and_status_changes_are_audit_logged(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, platform_admin_token)

    client.patch(
        f"/v1/dealers/{dealer_id}/users/{user['id']}",
        json={"employmentStatus": "terminated"},
        headers={**_bearer(platform_admin_token), "If-Match": "1"},
    )

    log = client.get(f"/v1/dealers/{dealer_id}/audit-log", headers=_bearer(platform_admin_token))
    items = log.json()["items"]
    user_events = [item for item in items if item["entityId"] == user["id"]]
    assert any(e["action"] == "create" for e in user_events)
    update_event = next(e for e in user_events if e["action"] == "update")
    assert update_event["after"]["employment_status"] == "terminated"
    assert update_event["after"]["status"] == "deactivated"


# --- pagination + filtering ---------------------------------------------------


def test_list_users_filters_by_role_and_status(client):
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    dealer_id = _create_dealer(client)
    _create_user(client, dealer_id, platform_admin_token, email="a@example.ch", role="sales", accessRole="sales")
    _create_user(
        client, dealer_id, platform_admin_token, email="b@example.ch", role="technician", accessRole="inventory"
    )

    response = client.get(f"/v1/dealers/{dealer_id}/users?role=sales", headers=_bearer(platform_admin_token))
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["role"] == "sales"
