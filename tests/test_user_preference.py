import uuid

from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

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
        group_id=dealer_group_of(str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Sam",
        "lastName": "Sales",
        "email": f"sam-{uuid.uuid4().hex[:8]}@example.ch",
        "role": "sales",
        "accessRoles": ["sales"],
        "isDealerManager": False,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _setup(client):
    """A dealer plus one real User row (needed: user_preference.user_id is
    FK-constrained to user.id) and that user's bearer token.
    """

    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"]))
    return _bearer(token)


def test_list_preferences_starts_empty(client):
    headers = _setup(client)
    response = client.get("/v1/me/preferences", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_get_unset_scope_returns_empty_not_404(client):
    headers = _setup(client)
    response = client.get("/v1/me/preferences/grid:mdm.customers.list", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "grid:mdm.customers.list"
    assert body["payload"] == {}
    assert body["updatedAt"] is None


def test_put_then_get_round_trips_arbitrary_payload(client):
    headers = _setup(client)
    body = {"schemaVersion": 1, "sidebarCollapsed": True, "density": "compact"}
    put_response = client.put("/v1/me/preferences/grid:mdm.stock.list", json=body, headers=headers)
    assert put_response.status_code == 200, put_response.text
    put_body = put_response.json()
    assert put_body["scope"] == "grid:mdm.stock.list"
    assert put_body["payload"]["schemaVersion"] == 1
    assert put_body["payload"]["sidebarCollapsed"] is True
    assert put_body["payload"]["density"] == "compact"
    assert put_body["updatedAt"] is not None

    get_response = client.get("/v1/me/preferences/grid:mdm.stock.list", headers=headers)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["payload"] == put_body["payload"]


def test_put_defaults_schema_version_to_one(client):
    headers = _setup(client)
    response = client.put("/v1/me/preferences/ui", json={"sidebarCollapsed": False}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["schemaVersion"] == 1


def test_second_put_overwrites_last_write_wins(client):
    headers = _setup(client)
    client.put("/v1/me/preferences/ui", json={"density": "compact"}, headers=headers)
    response = client.put("/v1/me/preferences/ui", json={"density": "comfortable"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["density"] == "comfortable"

    get_response = client.get("/v1/me/preferences/ui", headers=headers)
    assert get_response.json()["payload"]["density"] == "comfortable"


def test_list_reflects_multiple_scopes(client):
    headers = _setup(client)
    client.put("/v1/me/preferences/ui", json={"density": "compact"}, headers=headers)
    client.put(
        "/v1/me/preferences/grid:mdm.customers.list", json={"sort": [{"field": "lastName", "direction": "asc"}]},
        headers=headers,
    )
    response = client.get("/v1/me/preferences", headers=headers)
    assert response.status_code == 200, response.text
    scopes = {item["scope"] for item in response.json()["items"]}
    assert scopes == {"ui", "grid:mdm.customers.list"}


def test_delete_resets_to_default_and_is_idempotent(client):
    # A grid scope: an unset `ui` scope answers a starting language (KAN-104 item 2).
    headers = _setup(client)
    client.put("/v1/me/preferences/grid:mdm.stock.list", json={"density": "compact"}, headers=headers)

    delete_response = client.delete("/v1/me/preferences/grid:mdm.stock.list", headers=headers)
    assert delete_response.status_code == 204, delete_response.text

    get_response = client.get("/v1/me/preferences/grid:mdm.stock.list", headers=headers)
    assert get_response.json()["payload"] == {}
    assert get_response.json()["updatedAt"] is None

    # Deleting an already-absent scope is not an error.
    second_delete = client.delete("/v1/me/preferences/grid:mdm.stock.list", headers=headers)
    assert second_delete.status_code == 204, second_delete.text


def test_preferences_are_isolated_per_user(client):
    dealer_id = _create_dealer(client)
    user_a = _create_user(client, dealer_id)
    user_b = _create_user(client, dealer_id)
    headers_a = _bearer(
        _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user_a["id"]))
    )
    headers_b = _bearer(
        _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user_b["id"]))
    )

    client.put("/v1/me/preferences/grid:mdm.stock.list", json={"density": "compact"}, headers=headers_a)

    response_b = client.get("/v1/me/preferences/grid:mdm.stock.list", headers=headers_b)
    assert response_b.json()["payload"] == {}

    list_b = client.get("/v1/me/preferences", headers=headers_b)
    assert list_b.json() == {"items": []}


def test_oversized_payload_is_rejected(client):
    headers = _setup(client)
    body = {"schemaVersion": 1, "blob": "x" * (65 * 1024)}
    response = client.put("/v1/me/preferences/ui", json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unprocessable_entity"


def test_invalid_scope_is_rejected(client):
    headers = _setup(client)
    response = client.put("/v1/me/preferences/not a valid scope", json={"schemaVersion": 1}, headers=headers)
    assert response.status_code == 422, response.text


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/v1/me/preferences")
    assert response.status_code == 401, response.text


# --- KAN-104 item 2 (Anto, 2026-11-03, decision 1 B) ----------------------------------------------


def _betrieb_speaks(db_session, dealer_id: str, language: str) -> None:
    from app.core.i18n import SwissLanguage
    from app.platform.models.dealership import Dealership

    db_session.get(Dealership, uuid.UUID(dealer_id)).default_correspondence_language = SwissLanguage(language)
    db_session.commit()


def test_a_new_users_screen_starts_in_the_betriebs_correspondence_language(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _betrieb_speaks(db_session, dealer_id, "it")
    headers = _bearer(_token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))

    body = client.get("/v1/me/preferences/ui", headers=headers).json()

    assert body["payload"] == {"uiLanguage": "it"}
    assert body["updatedAt"] is None  # a starting value, never a stored choice
    # Only the `ui` scope starts that way; every other scope is still empty.
    assert client.get("/v1/me/preferences/grid:mdm.customers.list", headers=headers).json()["payload"] == {}


def test_the_users_own_choice_wins_over_the_betriebs_language(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _betrieb_speaks(db_session, dealer_id, "it")
    headers = _bearer(_token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))

    client.put("/v1/me/preferences/ui", json={"schemaVersion": 1, "uiLanguage": "fr"}, headers=headers)

    assert client.get("/v1/me/preferences/ui", headers=headers).json()["payload"]["uiLanguage"] == "fr"


def test_a_ui_row_without_a_language_reads_the_betriebs_language(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _betrieb_speaks(db_session, dealer_id, "fr")
    headers = _bearer(_token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    client.put("/v1/me/preferences/ui", json={"schemaVersion": 1, "density": "compact"}, headers=headers)

    payload = client.get("/v1/me/preferences/ui", headers=headers).json()["payload"]

    assert payload["density"] == "compact"
    assert payload["uiLanguage"] == "fr"


def test_a_null_language_and_the_list_read_the_betriebs_language_too(client, db_session):
    """One rule for every read (and for mail and push): a ui row whose
    uiLanguage is not a string reads the starting language, in the list too."""

    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _betrieb_speaks(db_session, dealer_id, "it")
    headers = _bearer(_token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    client.put("/v1/me/preferences/ui", json={"schemaVersion": 1, "uiLanguage": None}, headers=headers)

    assert client.get("/v1/me/preferences/ui", headers=headers).json()["payload"]["uiLanguage"] == "it"
    listed = {item["scope"]: item["payload"] for item in client.get("/v1/me/preferences", headers=headers).json()["items"]}
    assert listed["ui"]["uiLanguage"] == "it"
