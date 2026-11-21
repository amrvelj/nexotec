"""KAN-221 PR-4 — FR-A-17's exclusions and the user's notices (D-A-05,
D-A-24, FR-A-17, FR-A-18).

Under a support session the principal carries `acting_for`. It can never
change credentials or MFA, the manager flag, an integration secret or a
connected mailbox, nor start another impersonation: each such route answers
403 `impersonation_excluded` — the one deliberate 403, since the resource
exists and the actor is known. The routes are named one by one below, and a
route that carries the gate without being named fails too.

The token that sets `acting_for` is PR 3 (preserved `app/core/auth.py`); here
the principal is the dependency override, as PR 2's tests do.

The user is told when a session starts and when it ends; the manager is not.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.impersonation import IMPERSONATION_EXCLUDED, refuse_under_impersonation
from app.main import app
from app.platform.identity.models.notification import Notification
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.services import support_sessions
from tests.identity_support import PasswordLogin, create_dealership, create_user, platform_admin_headers
from tests.platform_tenant_support import create_staff
from tests.route_walk import api_routes

_ID = "019a0000-0000-7000-8000-000000000001"

# Every route FR-A-17 excludes outright, by method and path. A new credential,
# MFA, manager-flag, secret or impersonation route joins this list in its PR.
EXCLUDED: tuple[tuple[str, str], ...] = (
    # Credentials, MFA and sign-in state
    ("POST", "/v1/auth/mfa/totp/enrol"),
    ("POST", "/v1/auth/mfa/totp/confirm"),
    ("DELETE", "/v1/auth/mfa/totp"),
    ("POST", "/v1/auth/mfa/recovery-codes/regenerate"),
    ("POST", "/v1/auth/mfa/passkeys/options"),
    ("POST", "/v1/auth/mfa/passkeys"),
    ("DELETE", "/v1/auth/mfa/passkeys/{passkey_id}"),
    ("DELETE", "/v1/auth/sessions/{session_id}"),
    ("POST", "/v1/dealerships/{dealership_id}/users/{user_id}/invitations"),
    ("PUT", "/v1/dealerships/{dealership_id}/auth-config"),
    # The manager flag
    ("PUT", "/v1/dealerships/{dealership_id}/users/{user_id}/manager-flag"),
    ("PUT", "/v1/dealerships/{dealership_id}/users/{user_id}/memberships/{member_dealership_id}/manager-flag"),
    # Integration secrets and connected mailboxes (grants holding provider tokens)
    ("PUT", "/v1/integrations/connections/{connection_id}/secrets/{slot}"),
    ("DELETE", "/v1/integrations/connections/{connection_id}/secrets/{slot}"),
    ("POST", "/v1/dealerships/{dealership_id}/mail/business-mailbox/links"),
    ("DELETE", "/v1/dealerships/{dealership_id}/mail/business-mailbox/{mailbox_id}"),
    ("POST", "/v1/integrations/mail/own-mailbox/links"),
    ("DELETE", "/v1/integrations/mail/own-mailbox/{mailbox_id}"),
    ("POST", "/v1/integrations/mail/link-callback"),
    ("DELETE", "/v1/me/connected-accounts/{account_id}"),
    ("DELETE", "/v1/dealerships/{dealership_id}/users/{user_id}/connected-accounts/{account_id}"),
    # Deleting a connection drops every secret slot and its folder at once.
    ("DELETE", "/v1/integrations/connections/{connection_id}"),
    # Google Business Profile: the location link stores a refresh token; disconnect drops it (KAN-226).
    ("POST", "/v1/dealerships/{dealership_id}/business-profile/links"),
    ("POST", "/v1/integrations/business-profile/link-callback"),
    ("DELETE", "/v1/dealerships/{dealership_id}/business-profile/bindings/{binding_id}"),
    # A browser subscribing to the user's notices — the security family included —
    # would keep receiving them after the session ends.
    ("POST", "/v1/me/push-subscriptions"),
    ("DELETE", "/v1/me/push-subscriptions/{subscription_id}"),
    # Another impersonation
    ("POST", "/v1/platform/support-sessions"),
    # Granting or revoking platform_admin: the Platform users panel (PR 6).
    ("POST", "/v1/platform/staff"),
    ("POST", "/v1/platform/staff/{user_id}/revoke"),
)

# Excluded by their own staff gate before this PR (FR-AU-27): the recovery door.
RECOVERY: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/platform/users/{user_id}/recovery/reset-mfa"),
    ("POST", "/v1/platform/users/{user_id}/recovery/change-email"),
    ("POST", "/v1/platform/dealerships/{dealership_id}/recovery/grant-manager"),
)


# Deliberately open, with the reason: the login's own second-factor step runs
# before any session exists, so there is no principal and nothing to refuse.
OPEN_ON_PURPOSE: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/auth/mfa/verify"),
    ("POST", "/v1/auth/mfa/passkey/options"),
    ("POST", "/v1/auth/mfa/passkey/verify"),
)


def _acting(*, tenant_id: uuid.UUID, acting_for: uuid.UUID | None = None) -> Principal:
    """A staff member acting for a Betrieb's manager: the roles and the flag
    are the user's, the user id is the staff member's."""

    return Principal(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        memberships=frozenset({tenant_id}),
        active_location_id=None,
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
        amr=frozenset({"pwd", "otp", "mfa"}),
        mfa=True,
        acting_for=acting_for or uuid.uuid4(),
    )


@pytest.fixture()
def acting_as(client):
    def _use(principal: Principal) -> None:
        app.dependency_overrides[get_current_principal] = lambda: principal

    yield _use
    app.dependency_overrides.pop(get_current_principal, None)


def _call(client, method: str, path: str, tenant_id: uuid.UUID, json=None):
    url = path.replace("{dealership_id}", str(tenant_id))
    for name in ("user_id", "member_dealership_id", "passkey_id", "session_id", "connection_id", "mailbox_id", "account_id", "binding_id", "subscription_id"):
        url = url.replace("{" + name + "}", _ID)
    url = url.replace("{slot}", "api_key")
    headers = {"Idempotency-Key": str(uuid.uuid4()), "If-Match": "1"}
    return client.request(method, url, json=json if json is not None else {}, headers=headers)


def _excluded(response) -> bool:
    return response.status_code == 403 and (response.json()["error"].get("details") or {}).get("rule") == (
        IMPERSONATION_EXCLUDED
    )


def _carries_gate(dependant) -> bool:
    return any(d.call is refuse_under_impersonation or _carries_gate(d) for d in dependant.dependencies)


def test_the_named_routes_are_exactly_the_routes_that_carry_the_gate():
    walked = list(api_routes(app.routes))
    assert len(walked) > 100  # the walk sees the API (tests/route_walk.py)
    carrying = {
        (method, path) for path, route in walked if _carries_gate(route.dependant) for method in route.methods
    }
    assert carrying == set(EXCLUDED)


def test_the_routes_left_open_on_purpose_exist_and_carry_no_gate():
    walked = {(method, path): route for path, route in api_routes(app.routes) for method in route.methods}
    for key in OPEN_ON_PURPOSE:
        assert key in walked, key
        assert not _carries_gate(walked[key].dependant), key


@pytest.mark.parametrize(("method", "path"), EXCLUDED)
def test_an_excluded_route_refuses_a_support_session(client, acting_as, method, path):
    tenant_id = uuid.uuid4()
    acting_as(_acting(tenant_id=tenant_id))
    response = _call(client, method, path, tenant_id)
    assert _excluded(response), (response.status_code, response.text)


@pytest.mark.parametrize(("method", "path"), EXCLUDED)
def test_the_same_route_is_not_refused_without_a_support_session(client, acting_as, method, path):
    tenant_id = uuid.uuid4()
    principal = _acting(tenant_id=tenant_id)
    acting_as(Principal(**{**principal.__dict__, "acting_for": None}))
    response = _call(client, method, path, tenant_id)
    assert not _excluded(response), response.text


@pytest.mark.parametrize(("method", "path"), RECOVERY)
def test_the_recovery_door_refuses_a_support_session(client, acting_as, method, path):
    tenant_id = uuid.uuid4()
    principal = _acting(tenant_id=tenant_id)
    acting_as(Principal(**{**principal.__dict__, "roles": frozenset({AccessRole.PLATFORM_ADMIN})}))
    response = _call(client, method, path, tenant_id, json={"reason": "Ticket 4711", "email": "a@example.ch"})
    assert response.status_code == 403, response.text


# --- the manager flag and the sign-in address inside a broader write ----------------


@pytest.fixture()
def betrieb(client, mailbox):
    dealership = create_dealership(client)
    beat = create_user(client, dealership, isDealerManager=False)
    return uuid.UUID(dealership), beat


def test_a_user_edit_under_a_support_session_may_not_touch_the_manager_flag_or_the_address(client, acting_as, betrieb):
    tenant_id, beat = betrieb
    acting_as(_acting(tenant_id=tenant_id))
    path = f"/v1/dealerships/{tenant_id}/users/{beat['id']}"
    headers = {"If-Match": str(beat["version"])}
    for body in ({"isDealerManager": True}, {"email": "neu@example.ch"}, {"firstName": "B", "isDealerManager": False}):
        assert _excluded(client.patch(path, json=body, headers=headers)), body
    # The rest of the edit is the user's own work: full write (D-A-24).
    allowed = client.patch(path, json={"firstName": "Béatrice"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["firstName"] == "Béatrice"


def test_a_new_user_or_membership_under_a_support_session_is_never_a_manager(client, acting_as, betrieb):
    tenant_id, beat = betrieb
    acting_as(_acting(tenant_id=tenant_id))
    created = client.post(
        f"/v1/dealerships/{tenant_id}/users",
        json={
            "firstName": "Neu",
            "lastName": "Person",
            "email": "neu.person@example.ch",
            "role": "sales",
            "accessRoles": ["sales"],
            "isDealerManager": True,
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert _excluded(created), created.text
    granted = client.post(
        f"/v1/dealerships/{tenant_id}/users/{beat['id']}/memberships",
        json={"dealershipId": _ID, "isDealerManager": True},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert _excluded(granted), granted.text


def test_a_connection_edit_under_a_support_session_may_not_change_its_config(client, acting_as, betrieb):
    """A connection's config says where its stored secret is sent (an FTP host,
    an endpoint): a support session never changes it; the rest of the edit is
    refused only for what it is."""

    tenant_id, _beat = betrieb
    acting_as(_acting(tenant_id=tenant_id))
    path = f"/v1/integrations/connections/{_ID}"
    refused = client.patch(path, json={"config": {"ftpHost": "elsewhere.example"}}, headers={"If-Match": "1"})
    assert _excluded(refused), refused.text
    # Without config the edit reaches the route (no such connection here: 404).
    assert client.patch(path, json={"displayName": "Feed"}, headers={"If-Match": "1"}).status_code == 404


# --- the user's notices --------------------------------------------------------------


@pytest.fixture()
def scene(client, mailbox):
    login = PasswordLogin(client, mailbox)
    dealership = create_dealership(client)
    staff = create_staff(client)
    manager = create_user(client, dealership)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    headers = platform_admin_headers(tenant_id=uuid.UUID(dealership), user_id=uuid.UUID(staff["id"]), mfa=True)
    return dealership, headers, manager, beat


def _start(client, headers, target_user_id):
    response = client.post(
        "/v1/platform/support-sessions",
        json={"targetUserId": target_user_id, "reason": "Ticket 4711: die Offerte druckt nicht."},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _notices(db, user_id: str) -> list[Notification]:
    db.expire_all()
    return list(
        db.scalars(
            select(Notification).where(Notification.user_id == uuid.UUID(user_id)).order_by(Notification.created_at)
        )
    )


def test_the_user_is_told_when_a_session_starts_and_ends_and_the_manager_is_not(client, scene, db_session):
    dealership, headers, manager, beat = scene
    session = _start(client, headers, beat["id"])

    started = _notices(db_session, beat["id"])
    assert [n.event_code for n in started] == ["security.impersonation_started"]
    assert started[0].subject_type == "support_session" and started[0].subject_id == uuid.UUID(session["id"])
    assert started[0].dealership_id == uuid.UUID(dealership)
    # Never the reason or a name in the notice.
    assert started[0].params == {}

    ended = client.post(
        f"/v1/platform/support-sessions/{session['id']}/end",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert ended.status_code == 200, ended.text
    assert [n.event_code for n in _notices(db_session, beat["id"])] == [
        "security.impersonation_started",
        "security.impersonation_ended",
    ]
    # D-A-24: no manager notification.
    assert [n for n in _notices(db_session, manager["id"]) if n.event_code.startswith("security.impersonation")] == []


def test_a_timed_out_session_tells_the_user_when_its_ending_is_written(client, scene, db_session):
    _dealership, headers, _manager, beat = scene
    session = _start(client, headers, beat["id"])
    row = db_session.get(SupportSession, uuid.UUID(session["id"]))

    closed = support_sessions.close_timed_out_sessions(db_session, now=row.expires_at + dt.timedelta(seconds=1))
    db_session.commit()

    assert closed == 1
    assert [n.event_code for n in _notices(db_session, beat["id"])] == [
        "security.impersonation_started",
        "security.impersonation_ended",
    ]
