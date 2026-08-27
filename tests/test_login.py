"""Login/session tests. The login section below assumes Zitadel (WP-4) —
bootstraps a session via GET /v1/auth/oidc/callback with the oidc_fake
fixture (tests/fake_oidc.py) enqueuing a scripted identity, never a real
network call. The credential-CRUD section further down (POST .../credential)
retires in WP-4 commit 3/4 alongside app/platform/services/auth.py itself —
kept as-is here until that commit removes the endpoint it tests.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.platform.services.oidc import OidcError, ZitadelIdentity

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
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(
        f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(platform_admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login_via_oidc(client, oidc_fake, user: dict):
    """Bootstraps a session the way a real browser would: GET the callback
    endpoint after Zitadel has already authenticated the person — the fake
    client stands in for that exchange, never a real network call. Uses the
    same sub the user's own authIdentityId was created with, matching the
    real mapping app.platform.api.auth::oidc_callback performs.
    """

    oidc_fake.enqueue_identity(ZitadelIdentity(sub=user["authIdentityId"], email=user["email"], name=None))
    return client.get("/v1/auth/oidc/callback", follow_redirects=False)


# --- OIDC login/callback --------------------------------------------------------


def test_oidc_login_redirects_to_zitadel(client):
    response = client.get("/v1/auth/oidc/login", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_successful_callback_redirects_and_sets_httponly_session_cookie(client, oidc_fake):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    response = _login_via_oidc(client, oidc_fake, user)

    assert response.status_code in (302, 307)
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_response_never_contains_the_raw_token(client, oidc_fake):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    response = _login_via_oidc(client, oidc_fake, user)
    assert "token" not in response.text.lower()


def test_session_cookie_authenticates_subsequent_requests(client, oidc_fake):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    callback_response = _login_via_oidc(client, oidc_fake, user)
    token = callback_response.cookies.get("dms_session")
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


def test_revoked_zitadel_user_cannot_obtain_a_session(client, oidc_fake):
    """The whole point of WP-4's exit criterion: a revoked Zitadel user must
    not be able to obtain a Nexotec session. app.platform.services.oidc's
    complete_login calls Zitadel's userinfo endpoint live (not just the
    cached ID-token claims) precisely so a revocation surfaces here — this
    test scripts that live call failing, exactly as it would for a real
    revoked account, and asserts no session is ever minted.
    """

    dealer_id = _create_dealer(client)
    _create_user(client, dealer_id)
    oidc_fake.enqueue_error(OidcError("401 from Zitadel: account disabled"))

    response = client.get("/v1/auth/oidc/callback", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "dms_session=" not in response.headers.get("set-cookie", "")
    assert "/sign-in-error" in response.headers["location"]


def test_sub_not_provisioned_in_nexotec_is_rejected(client, oidc_fake):
    """Zitadel authenticating someone is not the same as Nexotec knowing who
    they are — provisioning stays entirely ours (no User row, no session),
    never auto-created from a successful external authentication.
    """

    oidc_fake.enqueue_identity(ZitadelIdentity(sub="a-sub-with-no-matching-user", email=None, name=None))

    response = client.get("/v1/auth/oidc/callback", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "dms_session=" not in response.headers.get("set-cookie", "")
    assert "/sign-in-error" in response.headers["location"]


# --- /auth/me (session restore after page reload) ------------------------------


def test_me_returns_current_user_via_cookie(client, oidc_fake):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    callback_response = _login_via_oidc(client, oidc_fake, user)
    token = callback_response.cookies.get("dms_session")

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


@pytest.mark.parametrize("status", ["suspended", "deactivated"])
def test_login_blocked_for_suspended_or_deactivated_user(client, oidc_fake, status):
    dealer_id = _create_dealer(client)
    # Not a manager — otherwise the WP-2 PR-2 "always at least one manager"
    # guard would correctly reject the status change below.
    user = _create_user(client, dealer_id, isDealerManager=False)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.patch(
        f"/v1/dealerships/{dealer_id}/users/{user['id']}",
        json={"status": status},
        headers={**_bearer(admin_token), "If-Match": "1"},
    )

    response = _login_via_oidc(client, oidc_fake, user)
    assert response.status_code in (302, 307)
    assert "dms_session=" not in response.headers.get("set-cookie", "")
    assert "/sign-in-error" in response.headers["location"]


# --- logout ----------------------------------------------------------------------


def test_logout_clears_cookie(client):
    response = client.post("/v1/auth/logout")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    # Cleared cookies are expired via Max-Age=0 or a past Expires date.
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


# --- dealership switcher (WP-3 PR-3) ----------------------------------------------


def test_login_response_includes_active_dealership_and_default_single_membership(client, oidc_fake):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    callback_response = _login_via_oidc(client, oidc_fake, user)
    token = callback_response.cookies.get("dms_session")

    client.cookies.set("dms_session", token)
    try:
        me = client.get("/v1/auth/me")
    finally:
        client.cookies.delete("dms_session")
    body = me.json()
    assert body["activeDealership"]["id"] == dealer_id
    assert [m["id"] for m in body["memberships"]] == [dealer_id]


def test_a_user_with_two_memberships_can_switch_active_dealership(client, oidc_fake, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    dealer_b = _create_dealer(client, dealerLicenseNumber="ZH-99999")

    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    callback_response = _login_via_oidc(client, oidc_fake, user)
    token = callback_response.cookies.get("dms_session")

    client.cookies.set("dms_session", token)
    try:
        me = client.get("/v1/auth/me")
    finally:
        client.cookies.delete("dms_session")
    membership_ids = {m["id"] for m in me.json()["memberships"]}
    assert membership_ids == {dealer_a, dealer_b}

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


def test_switching_to_a_dealership_outside_your_memberships_is_forbidden(client, oidc_fake):
    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    other_dealer = _create_dealer(client, dealerLicenseNumber="ZH-77777")

    callback_response = _login_via_oidc(client, oidc_fake, user)
    token = callback_response.cookies.get("dms_session")

    client.cookies.set("dms_session", token)
    try:
        response = client.post("/v1/auth/switch-dealership", json={"dealershipId": other_dealer})
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 403


def test_switch_dealership_requires_authentication(client):
    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": str(uuid.uuid4())})
    assert response.status_code == 401
