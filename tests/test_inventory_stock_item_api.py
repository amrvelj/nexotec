"""WP-7 PR-1: StockItem API."""

import uuid

from app.core.auth import AccessRole, create_access_token


def _token(role: AccessRole | None = None, is_dealer_manager: bool = False) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_requires_write_capability(client):
    token = _token()  # no roles, not a manager
    response = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Volkswagen Käfer", "condition": "used"},
        headers=_bearer(token),
    )
    assert response.status_code == 403, response.text


def test_create_and_get_stock_item(client):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Volkswagen Käfer 1303 LS Cabriolet", "condition": "used"},
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["lifecycleStatus"] == "pipeline"
    assert body["reservationState"] == "none"
    assert body["stockNumber"].startswith("S-")

    fetched = client.get(f"/v1/inventory/stock-items/{body['id']}", headers=_bearer(token))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == body["id"]


def test_patch_requires_if_match(client):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used"},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/inventory/stock-items/{created['id']}",
        json={"odometerKm": 42000},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["odometerKm"] == 42000


def test_stale_if_match_returns_409(client):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used"},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/inventory/stock-items/{created['id']}",
        json={"odometerKm": 42000},
        headers={**_bearer(token), "If-Match": str(created["version"] + 1)},
    )
    assert response.status_code == 409, response.text


def test_cross_tenant_get_is_404_not_403(client):
    owner_token = _token(role=AccessRole.INVENTORY)
    other_token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used"},
        headers=_bearer(owner_token),
    ).json()

    response = client.get(f"/v1/inventory/stock-items/{created['id']}", headers=_bearer(other_token))
    assert response.status_code == 404, response.text


def test_list_stock_items_filters_by_lifecycle_status(client):
    token = _token(role=AccessRole.INVENTORY)
    client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Pipeline car", "condition": "new"},
        headers=_bearer(token),
    )
    client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "In-stock car", "condition": "used", "vin": "1HGCM82633A004352"},
        headers=_bearer(token),
    )

    response = client.get("/v1/inventory/stock-items?lifecycle_status=in_stock", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["vehicleLabel"] == "In-stock car"
