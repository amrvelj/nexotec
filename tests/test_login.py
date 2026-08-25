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


def _create_dealer(client, **overrides) -> str:
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
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": "anna@example.ch",
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": True,
        "authIdentityId": "stub-sub-1",
    }
    payload.update(overrides)
    response = client.post(
        f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(platform_admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_credential(client, dealer_id: str, user_id: str, password: str, *, token: str | None = None):
    token = token or _token(AccessRole.PLATFORM_ADMIN)
    return client.post(
        f"/v1/dealerships/{dealer_id}/users/{user_id}/credential",
        json={"password": password},
        headers=_bearer(token),
    )


# --- credential set/reset -----------------------------------------------------


def test_dealer_admin_can_set_credential_for_own_tenant_user(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

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
    other_admin_token = _token(is_dealer_manager=True)  # different, random tenant_id
    response = _set_credential(client, dealer_id, user["id"], "correct horse battery staple", token=other_admin_token)
    assert response.status_code == 404


def test_credential_too_short_is_rejected(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    response = _set_credential(client, dealer_id, user["id"], "short")
    assert response.status_code == 422


def test_credential_set_and_reset_are_audit_logged(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    admin_token = _token(AccessRole.PLATFORM_ADMIN)

    _set_credential(client, dealer_id, user["id"], "correct horse battery staple", token=admin_token)
    _set_credential(client, dealer_id, user["id"], "a different password", token=admin_token)

    log = client.get(f"/v1/dealerships/{dealer_id}/audit-log", headers=_bearer(admin_token))
    assert log.status_code == 200
    events = [item for item in log.json()["items"] if item["entityId"] == user["id"]]
    actions = [item["action"] for item in events]
    assert "credential_set" in actions
    assert "credential_reset" in actions
    # Never logs the password or its hash.
    for item in events:
        assert "correct horse battery staple" not in str(item)
        assert "a different password" not in str(item)


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
        response = client.get(f"/v1/dealerships/{dealer_id}/users/{user['id']}")
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 200


# --- /auth/me (session restore after page reload) ------------------------------


def test_me_returns_current_user_via_cookie(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    login_response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    token = login_response.cookies.get("dms_session")

    client.cookies.set("dms_session", token)
    try:
        response = client.get("/v1/auth/me")
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == user["id"]


def test_me_without_session_is_401(client):
    response = client.get("/v1/auth/me")
    assert response.status_code == 401


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
    # Not a manager — otherwise the WP-2 PR-2 "always at least one manager"
    # guard would correctly reject the status change below.
    user = _create_user(client, dealer_id, isDealerManager=False)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.patch(
        f"/v1/dealerships/{dealer_id}/users/{user['id']}",
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


# --- dealership switcher (WP-3 PR-3) ----------------------------------------------


def test_login_response_includes_active_dealership_and_default_single_membership(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    _set_credential(client, dealer_id, user["id"], "correct horse battery staple")

    response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    body = response.json()
    assert body["activeDealership"]["id"] == dealer_id
    assert [m["id"] for m in body["memberships"]] == [dealer_id]


def test_a_user_with_two_memberships_can_switch_active_dealership(client, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    _set_credential(client, dealer_a, user["id"], "correct horse battery staple")
    dealer_b = _create_dealer(client, dealerLicenseNumber="ZH-99999")

    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    login_response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    membership_ids = {m["id"] for m in login_response.json()["memberships"]}
    assert membership_ids == {dealer_a, dealer_b}

    token = login_response.cookies.get("dms_session")
    client.cookies.set("dms_session", token)
    try:
        switch_response = client.post("/v1/auth/switch-dealership", json={"dealershipId": dealer_b})
    finally:
        client.cookies.delete("dms_session")

    assert switch_response.status_code == 200
    assert switch_response.json()["activeDealership"]["id"] == dealer_b

    new_token = switch_response.cookies.get("dms_session")
    assert new_token != token

    client.cookies.set("dms_session", new_token)
    try:
        me_response = client.get("/v1/auth/me")
        # The active dealership actually changed on the token itself, not
        # just in this one response body: require_tenant_match's own-tenant
        # check (GET /v1/dealerships/{id}) only passes when
        # principal.tenant_id == dealer_b, proving tenant_id really moved,
        # not just this endpoint's rendering of it. dealer_a is no longer
        # reachable the same way — the switch, not a second membership.
        dealer_b_response = client.get(f"/v1/dealerships/{dealer_b}")
        dealer_a_response = client.get(f"/v1/dealerships/{dealer_a}")
    finally:
        client.cookies.delete("dms_session")
    assert me_response.json()["activeDealership"]["id"] == dealer_b
    assert dealer_b_response.status_code == 200
    assert dealer_a_response.status_code == 404


def test_switching_to_a_dealership_outside_your_memberships_is_forbidden(client):
    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    _set_credential(client, dealer_a, user["id"], "correct horse battery staple")
    other_dealer = _create_dealer(client, dealerLicenseNumber="ZH-77777")

    login_response = client.post(
        "/v1/auth/login", json={"email": "anna@example.ch", "password": "correct horse battery staple"}
    )
    token = login_response.cookies.get("dms_session")

    client.cookies.set("dms_session", token)
    try:
        response = client.post("/v1/auth/switch-dealership", json={"dealershipId": other_dealer})
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 403


def test_switch_dealership_requires_authentication(client):
    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": str(uuid.uuid4())})
    assert response.status_code == 401
