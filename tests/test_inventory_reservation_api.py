"""WP-7 PR-4: reservation API — Idempotency-Key is required, not optional."""

import uuid

from app.core.auth import AccessRole, create_access_token


def _token(role: AccessRole | None = None) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_reserve_requires_idempotency_key_header(client):
    token = _token(AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items", json={"vehicleLabel": "Škoda Octavia", "condition": "new"}, headers=_bearer(token)
    ).json()

    response = client.post(
        f"/v1/inventory/stock-items/{created['id']}/reservations",
        json={"contractId": str(uuid.uuid4())},
        headers=_bearer(token),
    )
    assert response.status_code == 400, response.text


def test_reserve_then_release_via_api(client):
    token = _token(AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items", json={"vehicleLabel": "Škoda Octavia", "condition": "new"}, headers=_bearer(token)
    ).json()

    reserved = client.post(
        f"/v1/inventory/stock-items/{created['id']}/reservations",
        json={"contractId": str(uuid.uuid4())},
        headers={**_bearer(token), "Idempotency-Key": "test-key-1"},
    )
    assert reserved.status_code == 201, reserved.text
    reservation_id = reserved.json()["reservationId"]

    second = client.post(
        f"/v1/inventory/stock-items/{created['id']}/reservations",
        json={"contractId": str(uuid.uuid4())},
        headers={**_bearer(token), "Idempotency-Key": "test-key-2"},
    )
    assert second.status_code == 409, second.text

    released = client.post(
        f"/v1/inventory/reservations/{reservation_id}/release",
        headers={**_bearer(token), "Idempotency-Key": "release-key-1"},
    )
    assert released.status_code == 200, released.text
