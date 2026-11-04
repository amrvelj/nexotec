"""WP-7 PR-1: StockItem API."""

import uuid

from app.core.auth import AccessRole, create_access_token
from tests.configuration_test_support import stock_configuration_id
from tests.token_support import dealer_group_of


def _token(role: AccessRole | None = None, is_dealer_manager: bool = False) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=dealer_group_of(str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_requires_write_capability(client, db_session):
    token = _token()  # no roles, not a manager
    response = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Volkswagen Käfer", "condition": "used", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    )
    assert response.status_code == 403, response.text


def test_create_and_get_stock_item(client, db_session):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Volkswagen Käfer 1303 LS Cabriolet", "condition": "used", "configurationId": stock_configuration_id(db_session, token)},
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


def test_patch_requires_if_match(client, db_session):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/inventory/stock-items/{created['id']}",
        json={"odometerKm": 42000},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["odometerKm"] == 42000


def test_stale_if_match_returns_409(client, db_session):
    token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/inventory/stock-items/{created['id']}",
        json={"odometerKm": 42000},
        headers={**_bearer(token), "If-Match": str(created["version"] + 1)},
    )
    assert response.status_code == 409, response.text


def test_cross_tenant_get_is_404_not_403(client, db_session):
    owner_token = _token(role=AccessRole.INVENTORY)
    other_token = _token(role=AccessRole.INVENTORY)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used", "configurationId": stock_configuration_id(db_session, owner_token)},
        headers=_bearer(owner_token),
    ).json()

    response = client.get(f"/v1/inventory/stock-items/{created['id']}", headers=_bearer(other_token))
    assert response.status_code == 404, response.text


def test_list_stock_items_filters_by_lifecycle_status(client, db_session):
    token = _token(role=AccessRole.INVENTORY)
    # KAN-556 (ADR-117 (2)): a configured car is named by its configuration,
    # so the rows are told apart by id, not by a label the client sent.
    client.post(
        "/v1/inventory/stock-items",
        json={"condition": "new", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    )
    in_stock = client.post(
        "/v1/inventory/stock-items",
        json={"condition": "used", "vin": "1HGCM82633A004352", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    ).json()

    response = client.get("/v1/inventory/stock-items?lifecycle_status=in_stock", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["id"] for i in body["items"]] == [in_stock["id"]]


def test_list_stock_items_filters_by_condition(client, db_session):
    """KAN-458: «Zustand = Neu» on the Stock grid is applied by the server —
    the grid offered it and never sent it, so used cars stayed listed."""
    token = _token(role=AccessRole.INVENTORY)
    other_tenant = _token(role=AccessRole.INVENTORY)
    ids = {}
    for condition in ("new", "used", "demo"):
        ids[condition] = client.post(
            "/v1/inventory/stock-items",
            json={"condition": condition, "configurationId": stock_configuration_id(db_session, token)},
            headers=_bearer(token),
        ).json()["id"]
    client.post(
        "/v1/inventory/stock-items",
        json={"condition": "new", "configurationId": stock_configuration_id(db_session, other_tenant)},
        headers=_bearer(other_tenant),
    )

    response = client.get("/v1/inventory/stock-items?condition=new", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["id"] for i in body["items"]] == [ids["new"]]
    assert body["total"] == 1


def test_list_stock_items_combines_condition_with_lifecycle_status(client, db_session):
    token = _token(role=AccessRole.INVENTORY)
    client.post("/v1/inventory/stock-items", json={"condition": "new", "configurationId": stock_configuration_id(db_session, token)}, headers=_bearer(token))
    new_in_stock = client.post(
        "/v1/inventory/stock-items",
        json={"condition": "new", "vin": "1HGCM82633A004352", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    ).json()
    client.post(
        "/v1/inventory/stock-items",
        json={"condition": "used", "vin": "WVWZZZ1JZXW000001", "configurationId": stock_configuration_id(db_session, token)},
        headers=_bearer(token),
    )

    response = client.get("/v1/inventory/stock-items?condition=new&lifecycle_status=in_stock", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert [i["id"] for i in response.json()["items"]] == [new_in_stock["id"]]


def test_list_stock_items_refuses_an_unknown_condition(client):
    token = _token(role=AccessRole.INVENTORY)
    response = client.get("/v1/inventory/stock-items?condition=mint", headers=_bearer(token))
    assert response.status_code == 422, response.text
