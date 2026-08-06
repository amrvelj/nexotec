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


def _create_user(client, dealer_id: str, **overrides) -> dict:
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": "anna@example.ch",
        "role": "admin",
        "accessRole": "dealer_admin",
        "authIdentityId": "stub-sub-1",
    }
    payload.update(overrides)
    response = client.post(
        f"/v1/dealers/{dealer_id}/users", json=payload, headers=_bearer(platform_admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_credential(client, dealer_id: str, user_id: str, password: str, *, token: str | None = None):
    token = token or _token(AccessRole.PLATFORM_ADMIN)
    return client.post(
        f"/v1/dealers/{dealer_id}/users/{user_id}/credential",
        json={"password": password},
        headers=_bearer(token),
    )


# --- credential set/reset -----------------------------------------------------


def test_dealer_admin_can_set_credential_for_own_tenant_user(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    response = _set_credential(client, dealer_id, user["id"], "correct horse battery staple", token=token)
    assert response.status_code == 204


def test_platform_admin_can_set_credential_any_tenant(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    response = _set_credential(client, dealer_id, user["id"], "correct horse battery staple")
    assert response.status_code == 204


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_admin_roles_cannot_set_credential(client, role):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = _set_credential(client, dealer_id, user["id"], "correct horse battery staple", token=token)
    assert response.status_code == 403


def test_dealer_admin_cannot_set_credential_for_other_tenant(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    other_admin_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant_id
    response = _set_credential(client, dealer_id, user["id"], "correct horse battery staple", token=other_admin_token)
    assert response.status_code == 404


def test_credential_too_short_is_rejected(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    response = _set_credential(client, dealer_id, user["id"], "short")
    assert response.status_code == 422


def test_resetting_credential_clears_lockout(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    for _ in range(5):
        client.post("/v1/auth/login", json={"email": "anna@example.ch", "password": "wrong-password"})

    locked = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert locked.status_code == 401

    _set_credential(client, dealer_id, user["id"], "a brand new password")
    unlocked = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "a brand new password"}
    )
    assert unlocked.status_code == 200


# --- login ---------------------------------------------------------------------


def test_login_with_correct_password_succeeds(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == user["id"]
    assert body["user"]["email"] == "anna@example.ch"
    assert "password" not in body["user"]


def test_login_sets_httponly_session_cookie(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_response_body_never_contains_the_raw_token(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert "token" not in response.text.lower()


def test_session_cookie_authenticates_subsequent_requests(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    login_response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert login_response.status_code == 200
    token = login_response.cookies.get("dms_session")
    assert token is not None

    # Exercise the cookie fallback in get_bearer_token directly against a
    # protected endpoint, using the raw cookie value (TestClient's own
    # cookie jar doesn't reliably resend `Secure` cookies over the plain-http
    # test transport, so this asserts on the mechanism rather than relying
    # on the jar's behavior).
    client.cookies.set("dms_session", token)
    try:
        response = client.get(f"/v1/dealers/{dealer_id}/users/{user['id']}")
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 200


def test_login_with_wrong_password_is_401(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    response = client.post("/v1/auth/login", json={"email": "anna@example.ch", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_login_with_unknown_email_is_401_generic(client):
    response = client.post(
        "/v1/auth/login", json={"email": "nobody@example.ch", "password": "whatever12345"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password."


def test_login_for_user_with_no_credential_set_is_401_generic(client):
    dealer_id = _create_dealer(client)
    _create_user(client, dealer_id)
    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "whatever12345"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password."


def test_account_locks_after_max_failed_attempts(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    for _ in range(5):
        response = client.post(
            "/v1/auth/login", json={"email": "anna@example.ch", "password": "wrong-password"}
        )
        assert response.status_code == 401

    locked_response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert locked_response.status_code == 401
    assert "locked" in locked_response.json()["error"]["message"].lower()


def test_successful_login_resets_failed_attempts(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    for _ in range(3):
        client.post("/v1/auth/login", json={"email": "anna@example.ch", "password": "wrong"})

    ok = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert ok.status_code == 200

    for _ in range(3):
        response = client.post("/v1/auth/login", json={"email": "anna@example.ch", "password": "wrong"})
        assert response.status_code == 401

    still_ok = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert still_ok.status_code == 200


@pytest.mark.parametrize("status", ["suspended", "deactivated"])
def test_login_blocked_for_suspended_or_deactivated_user(client, status):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.patch(
        f"/v1/dealers/{dealer_id}/users/{user['id']}",
        json={"status": status},
        headers={**_bearer(admin_token), "If-Match": "1"},
    )

    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    assert response.status_code == 403


# --- logout ----------------------------------------------------------------------


def test_logout_clears_cookie(client):
    response = client.post("/v1/auth/logout")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    # Cleared cookies are expired via Max-Age=0 or a past Expires date.
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
