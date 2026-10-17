"""Login/session tests (KAN-196: Nexotec owns the front door, D-A-07).
A user is created with an invitation, accepts it through the recorded mail
(the `mailbox` fixture) and signs in with `POST /v1/auth/login`; the
session cookie is the same httpOnly RS256 token as before. The uniform
refusal, throttling, tokens and MFA have their own modules
(tests/test_identity_*.py); this one covers the session layer: cookie,
/auth/me, logout, switch-dealership, the per-dealership manager flag.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token, get_current_principal
from app.platform.services.identity import INVALID_CREDENTIALS
from tests.dealership_support import valid_uid
from tests.identity_support import PasswordLogin, new_email, set_mfa_enforced

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
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    # KAN-196: these tests sign in with a password alone; a Betrieb that
    # enforces a second factor confines such a session to enrolment.
    set_mfa_enforced(client, response.json()["id"], False)
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    platform_admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": new_email("anna"),
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": True,
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(platform_admin_token))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


# --- login ----------------------------------------------------------------------


def test_a_successful_login_sets_the_httponly_session_cookie(client, login):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    response = login.login(user)

    assert response.status_code == 200, response.text
    assert response.json()["mfaRequired"] is False
    assert response.json()["session"]["user"]["id"] == user["id"]
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_response_never_contains_the_raw_token(client, login):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    response = login.login(user)
    token = response.cookies.get("dms_session")
    assert token and token not in response.text
    assert "token" not in response.text.lower()


def test_session_cookie_authenticates_subsequent_requests(client, login):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    token = login.session_cookie(user)

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


def test_an_unknown_address_gets_the_uniform_401(client):
    """Nexotec knowing who someone is stays provisioning's business: no User
    row, no session — and the answer is the one every refusal gets."""

    response = client.post("/v1/auth/login", json={"email": "nobody@example.ch", "password": "whatever-it-is-12"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == INVALID_CREDENTIALS
    assert "dms_session=" not in response.headers.get("set-cookie", "")


# --- /auth/me (session restore after page reload) ------------------------------


def test_me_returns_current_user_via_cookie(client, login):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    token = login.session_cookie(user)

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
def test_login_blocked_for_suspended_or_deactivated_user(client, login, status):
    dealer_id = _create_dealer(client)
    # Not a manager — otherwise the WP-2 PR-2 "always at least one manager"
    # guard would correctly reject the status change below.
    user = _create_user(client, dealer_id, isDealerManager=False)
    login.accept_invitation(user)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.patch(
        f"/v1/dealerships/{dealer_id}/users/{user['id']}",
        json={"status": status},
        headers={**_bearer(admin_token), "If-Match": "2"},
    )
    assert response.status_code == 200, response.text

    response = login.login(user)
    assert response.status_code == 401
    # The same body an unknown address or a wrong password gets (FR-AU-16).
    assert response.json()["error"]["message"] == INVALID_CREDENTIALS
    assert "dms_session=" not in response.headers.get("set-cookie", "")


# --- logout ----------------------------------------------------------------------


def test_logout_clears_cookie(client):
    response = client.post("/v1/auth/logout")
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "dms_session=" in set_cookie
    # Cleared cookies are expired via Max-Age=0 or a past Expires date.
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


# --- dealership switcher (WP-3 PR-3) ----------------------------------------------


def test_login_response_includes_active_dealership_and_default_single_membership(client, login):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)

    token = login.session_cookie(user)

    client.cookies.set("dms_session", token)
    try:
        me = client.get("/v1/auth/me")
    finally:
        client.cookies.delete("dms_session")
    body = me.json()
    assert body["activeDealership"]["id"] == dealer_id
    assert [m["id"] for m in body["memberships"]] == [dealer_id]


def test_a_user_with_two_memberships_can_switch_active_dealership(client, login, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    dealer_b = _create_dealer(client)

    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    token = login.session_cookie(user)

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


def test_switching_to_a_dealership_outside_your_memberships_is_forbidden(client, login):
    dealer_a = _create_dealer(client)
    user = _create_user(client, dealer_a)
    other_dealer = _create_dealer(client)

    token = login.session_cookie(user)

    client.cookies.set("dms_session", token)
    try:
        response = client.post("/v1/auth/switch-dealership", json={"dealershipId": other_dealer})
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 403


def test_switch_dealership_requires_authentication(client):
    response = client.post("/v1/auth/switch-dealership", json={"dealershipId": str(uuid.uuid4())})
    assert response.status_code == 401


# --- switch-dealership re-checks the database, never the token (KAN-141) ------------


def _switch_with(client, token: str, dealership_id: str):
    client.cookies.set("dms_session", token)
    try:
        return client.post("/v1/auth/switch-dealership", json={"dealershipId": dealership_id})
    finally:
        client.cookies.delete("dms_session")


def _user_with_a_sister_membership(client, db_session) -> tuple[str, str, dict]:
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    # Not a manager — otherwise the "always at least one manager" guard
    # would correctly reject the status change some of these tests make.
    user = _create_user(client, dealer_a, isDealerManager=False)
    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()
    return dealer_a, dealer_b, user


@pytest.mark.parametrize("target", ["home", "sister"])
@pytest.mark.parametrize("status", ["suspended", "deactivated"])
def test_switch_dealership_refuses_a_user_made_inactive_after_login(client, login, db_session, status, target):
    """Dealer Administration FR-A-10: no window in which a deactivated user
    still holds a working session. Switching — even back to the dealership
    the token is already in — would otherwise mint a fresh token with a new
    expiry, renewing the session indefinitely past the deactivation.
    """

    dealer_a, dealer_b, user = _user_with_a_sister_membership(client, db_session)
    token = login.session_cookie(user)
    assert token

    response = client.patch(
        f"/v1/dealerships/{dealer_a}/users/{user['id']}",
        json={"status": status},
        headers={**_bearer(_token(AccessRole.PLATFORM_ADMIN)), "If-Match": "2"},
    )
    assert response.status_code == 200, response.text

    response = _switch_with(client, token, dealer_a if target == "home" else dealer_b)
    assert response.status_code == 401
    # KAN-196: the status change ended the session row itself (FR-AU-17), so
    # the session guard refuses the token before the switch is even looked at.
    assert response.json()["error"]["message"] == "Your session has ended — sign in again."
    assert "dms_session=" not in response.headers.get("set-cookie", "")

    # The minting path still refuses on its own (KAN-141): a token without a
    # session row — the shape a test or a bootstrap mints — is judged by the
    # account's status, never by the claims it carries.
    from app.platform.models.user import User

    row = db_session.get(User, uuid.UUID(user["id"]))
    bare = create_access_token(
        user_id=row.id,
        tenant_id=uuid.UUID(dealer_a),
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}),
        memberships={uuid.UUID(dealer_a), uuid.UUID(dealer_b)},
    )
    response = _switch_with(client, bare, dealer_a if target == "home" else dealer_b)
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "This user account is not active."


def test_switch_dealership_refuses_a_membership_revoked_after_login(client, login, db_session):
    from sqlalchemy import delete

    from app.platform.models.dealership_membership import DealershipMembership

    _, dealer_b, user = _user_with_a_sister_membership(client, db_session)
    token = login.session_cookie(user)

    db_session.execute(
        delete(DealershipMembership).where(
            DealershipMembership.user_id == uuid.UUID(user["id"]),
            DealershipMembership.dealership_id == uuid.UUID(dealer_b),
        )
    )
    db_session.commit()
    # The token the caller still holds keeps claiming the membership.
    assert uuid.UUID(dealer_b) in get_current_principal(token).memberships

    response = _switch_with(client, token, dealer_b)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "This dealership is not one of your memberships."
    assert "dms_session=" not in response.headers.get("set-cookie", "")


def test_switch_dealership_allows_a_membership_granted_after_login(client, login, db_session):
    """The database is the authority both ways: a membership granted after
    login is switchable without signing in again, although the token's
    memberships claim does not list it yet.
    """

    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    user = _create_user(client, dealer_a)
    token = login.session_cookie(user)
    assert uuid.UUID(dealer_b) not in get_current_principal(token).memberships

    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    response = _switch_with(client, token, dealer_b)
    assert response.status_code == 200, response.text
    assert response.json()["activeDealership"]["id"] == dealer_b
    assert {m["id"] for m in response.json()["memberships"]} == {dealer_a, dealer_b}


# --- GET /v1/auth/me lists memberships from the database (KAN-237) ----------------


def _me_with(client, token: str):
    client.cookies.set("dms_session", token)
    try:
        return client.get("/v1/auth/me")
    finally:
        client.cookies.delete("dms_session")


def test_auth_me_stops_listing_a_membership_revoked_after_login(client, login, db_session):
    """The switcher offers what /auth/me lists. Listed from the token's
    memberships claim, a revoked membership stayed on offer until the token
    expired, and choosing it was refused (403) — the same home +
    dealership_membership rule switch-dealership mints from applies here.
    """

    from sqlalchemy import delete

    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a, dealer_b, user = _user_with_a_sister_membership(client, db_session)
    token = login.session_cookie(user)
    assert token
    assert {m["id"] for m in _me_with(client, token).json()["memberships"]} == {dealer_a, dealer_b}

    db_session.execute(
        delete(DealershipMembership).where(
            DealershipMembership.user_id == uuid.UUID(user["id"]),
            DealershipMembership.dealership_id == uuid.UUID(dealer_b),
        )
    )
    db_session.commit()
    # The token still claims it; /auth/me must not.
    assert uuid.UUID(dealer_b) in get_current_principal(token).memberships

    response = _me_with(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"user", "activeDealership", "memberships", "entitlements", "mfaEnrolmentRequired"}
    assert body["activeDealership"]["id"] == dealer_a
    assert [m["id"] for m in body["memberships"]] == [dealer_a]
    assert set(body["memberships"][0]) == {"id", "legalName"}


def test_auth_me_lists_a_membership_granted_after_login(client, login, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    user = _create_user(client, dealer_a)
    token = login.session_cookie(user)
    assert token
    assert uuid.UUID(dealer_b) not in get_current_principal(token).memberships

    db_session.add(DealershipMembership(user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    body = _me_with(client, token).json()
    # Active dealership first, then the rest — unchanged ordering rule.
    assert [m["id"] for m in body["memberships"]] == [dealer_a, dealer_b]


# --- the manager flag is held per dealership (KAN-98, D-A-01) ----------------------


def _session_cookie_for_dealership(client, login, user: dict, dealership_id: str) -> str:
    """Logs in (home dealership) and switches to `dealership_id`, returning
    the session token the switch minted.
    """

    token = login.session_cookie(user)
    client.cookies.set("dms_session", token)
    try:
        response = client.post("/v1/auth/switch-dealership", json={"dealershipId": dealership_id})
    finally:
        client.cookies.delete("dms_session")
    assert response.status_code == 200, response.text
    return response.cookies.get("dms_session")


def _create_user_as(client, token: str, dealership_id: str, email: str):
    client.cookies.set("dms_session", token)
    try:
        return client.post(
            f"/v1/dealerships/{dealership_id}/users",
            json={
                "firstName": "Beat",
                "lastName": "Neu",
                "email": email,
                "role": "sales",
                "accessRoles": ["sales"],
                "isDealerManager": False,
            },
        )
    finally:
        client.cookies.delete("dms_session")


def test_a_home_manager_with_a_plain_membership_is_not_a_manager_in_the_sister_dealership(
    client, login, db_session
):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    manager_of_a = _create_user(client, dealer_a)  # isDealerManager=True on the home dealership
    db_session.add(DealershipMembership(user_id=uuid.UUID(manager_of_a["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    token_in_b = _session_cookie_for_dealership(client, login, manager_of_a, dealer_b)

    # require_write("dealership_users") — manager-only — refuses them in B.
    response = _create_user_as(client, token_in_b, dealer_b, "beat.b@example.ch")
    assert response.status_code == 403


def test_a_membership_that_holds_the_manager_flag_is_a_manager_in_that_dealership(client, login, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    user = _create_user(client, dealer_a, isDealerManager=False, email="nomgr@example.ch")
    # dealer_a keeps a manager of its own; this user is a manager in B only.
    _create_user(client, dealer_a, email="home.mgr@example.ch")
    db_session.add(
        DealershipMembership(
            user_id=uuid.UUID(user["id"]), dealership_id=uuid.UUID(dealer_b), is_dealer_manager=True
        )
    )
    db_session.commit()

    token_in_b = _session_cookie_for_dealership(client, login, user, dealer_b)
    assert _create_user_as(client, token_in_b, dealer_b, "beat.b@example.ch").status_code == 201

    token_in_a = _session_cookie_for_dealership(client, login, user, dealer_a)
    assert _create_user_as(client, token_in_a, dealer_a, "beat.a@example.ch").status_code == 403


def test_switching_back_to_the_home_dealership_restores_the_home_manager_flag(client, login, db_session):
    from app.platform.models.dealership_membership import DealershipMembership

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    manager_of_a = _create_user(client, dealer_a)
    db_session.add(DealershipMembership(user_id=uuid.UUID(manager_of_a["id"]), dealership_id=uuid.UUID(dealer_b)))
    db_session.commit()

    _session_cookie_for_dealership(client, login, manager_of_a, dealer_b)
    token_in_a = _session_cookie_for_dealership(client, login, manager_of_a, dealer_a)

    assert _create_user_as(client, token_in_a, dealer_a, "beat.a@example.ch").status_code == 201
