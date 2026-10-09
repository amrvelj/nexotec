"""WP-7 PR-4: reservation API.

The Idempotency-Key was required here until KAN-266 moved both endpoints onto
IdempotentRoute (Anto's ruling, 2026-10-08): it is optional now, as on every
POST. Replay under a key is pinned in tests/test_inventory_idempotency.py."""

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


def test_reserve_without_a_key_is_accepted_and_a_second_reserve_is_still_a_conflict(client):
    """Without a key nothing is replayed, but a car is never reserved twice:
    the second reserve finds it reserved."""

    token = _token(AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items", json={"vehicleLabel": "Škoda Octavia", "condition": "new"}, headers=_bearer(token)
    ).json()
    path = f"/v1/inventory/stock-items/{created['id']}/reservations"
    body = {"contractId": str(uuid.uuid4())}

    first = client.post(path, json=body, headers=_bearer(token))
    second = client.post(path, json=body, headers=_bearer(token))

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text


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
