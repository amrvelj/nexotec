"""Helpers shared by the KAN-199 tests: dealerships and users through the API,
tokens for platform staff and dealer managers."""

import uuid

from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
    "country": "CH",
}


def token(
    role: AccessRole | None = None,
    *,
    tenant_id: uuid.UUID | str | None = None,
    user_id: uuid.UUID | str | None = None,
    is_dealer_manager: bool = False,
) -> str:
    tid = uuid.UUID(str(tenant_id)) if tenant_id else uuid.uuid4()
    return create_access_token(
        user_id=uuid.UUID(str(user_id)) if user_id else uuid.uuid4(),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def bearer(tok: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}", **headers}


def platform_admin() -> dict[str, str]:
    return bearer(token(AccessRole.PLATFORM_ADMIN))


def manager_of(dealership_id: str, user: dict) -> dict[str, str]:
    """A dealer manager's headers: the token names the user row, so the
    service can look up which sister dealerships they manage."""

    return bearer(token(tenant_id=dealership_id, user_id=user["id"], is_dealer_manager=True))


def create_dealership(client, legal_name: str = "Garage Musterbetrieb AG", *, group_of: str | None = None) -> str:
    """A dealership; with `group_of`, a sister in that dealership's dealer
    group (ADR-014) — the shape a membership is for."""

    payload = {
        "legalName": legal_name,
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    if group_of is not None:
        home = client.get(f"/v1/dealerships/{group_of}", headers=platform_admin())
        assert home.status_code == 200, home.text
        payload["dealerGroupId"] = home.json()["dealerGroupId"]
    response = client.post(
        "/v1/dealerships",
        json=payload,
        headers={**platform_admin(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_user(client, dealership_id: str, *, is_dealer_manager: bool = True, **overrides) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": f"anna-{uuid.uuid4().hex[:8]}@example.ch",
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": is_dealer_manager,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(
        f"/v1/dealerships/{dealership_id}/users",
        json=payload,
        headers={**platform_admin(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def grant(client, home: str, user: dict, target: str, *, headers: dict | None = None, is_dealer_manager=False):
    return client.post(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships",
        json={"dealershipId": target, "isDealerManager": is_dealer_manager},
        headers={**(headers or platform_admin()), "Idempotency-Key": str(uuid.uuid4())},
    )


def audit_actions(client, dealership_id: str, user_id: str) -> list[dict]:
    response = client.get(f"/v1/dealerships/{dealership_id}/audit-log", headers=platform_admin())
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"] if item["entityId"] == user_id]


def user_version(client, dealership_id: str, user: dict) -> int:
    response = client.get(f"/v1/dealerships/{dealership_id}/users/{user['id']}", headers=platform_admin())
    assert response.status_code == 200, response.text
    return response.json()["version"]


def if_match(client, dealership_id: str, user: dict, **headers: str) -> dict[str, str]:
    return {**platform_admin(), **headers, "If-Match": str(user_version(client, dealership_id, user))}
