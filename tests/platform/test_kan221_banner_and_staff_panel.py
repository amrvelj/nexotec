"""KAN-221 PR-6 — the banner's read, the log's newest-first order and the
Platform users panel (D-A-05, FR-A-15, FR-A-17, FR-A-18; ADR-188).

- `GET /v1/me/support-session`: the session running on the caller, for the
  banner — the supported user sees it; nobody else; none once it is over.
  Under `acting_for` (PR 3) the staff member sees the same session.
- The two logs page newest first with `sort=startedAt:desc`.
- `/v1/platform/staff`: staff only; grant = a staff account in the platform
  tenant; revoke = deactivation there, the role removed for a legacy holder;
  never oneself, never the last active holder.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, Principal, get_current_principal
from app.main import app
from app.platform.identity.models.support_session import SupportSession
from app.platform.identity.models.user import User, UserStatus
from app.platform.organisation.models.dealership import PLATFORM_DEALERSHIP_ID
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)
from tests.platform_tenant_support import create_legacy_staff, create_staff

REASON = "Die Offerte druckt das Logo nicht — Ticket 4712."
ME = "/v1/me/support-session"
STAFF = "/v1/platform/staff"


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _staff_headers(staff: dict, *, mfa: bool = True, tenant_id: str | None = None) -> dict[str, str]:
    return platform_admin_headers(
        tenant_id=uuid.UUID(tenant_id) if tenant_id else PLATFORM_DEALERSHIP_ID, user_id=uuid.UUID(staff["id"]), mfa=mfa
    )


def _start(client, staff, dealership, target_user_id) -> dict:
    response = client.post(
        "/v1/platform/support-sessions",
        json={"targetUserId": target_user_id, "reason": REASON},
        headers={**_staff_headers(staff, tenant_id=dealership), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def scene(client, login):
    home = create_dealership(client)
    staff = create_staff(client, firstName="Petra", lastName="Support")
    manager = create_user(client, home, firstName="Maria", lastName="Chefin")
    beat = create_user(client, home, firstName="Beat", lastName="Verkauf", isDealerManager=False)
    for user in (manager, beat):
        login.accept_invitation(user)
    return {"home": home, "staff": staff, "manager": manager, "beat": beat}


# --- the banner's read --------------------------------------------------------------


def test_the_supported_user_reads_the_running_session_with_staff_reason_and_expiry(client, scene, login):
    session = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    response = client.get(ME, headers=cookie_headers(login.session_cookie(scene["beat"])))
    assert response.status_code == 200, response.text
    banner = response.json()["session"]
    assert banner["id"] == session["id"]
    assert banner["viewer"] == "user"
    assert banner["staffName"] == "Petra Support"
    assert banner["targetUserName"] == "Beat Verkauf"
    assert banner["reason"] == REASON
    assert banner["expiresAt"] == session["expiresAt"]


def test_nobody_else_reads_it(client, scene, login):
    _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    response = client.get(ME, headers=cookie_headers(login.session_cookie(scene["manager"])))
    assert response.status_code == 200, response.text
    assert response.json() == {"session": None}


def test_an_ended_or_timed_out_session_is_no_banner(client, scene, login, db_session):
    beat_cookie = cookie_headers(login.session_cookie(scene["beat"]))
    session = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    ended = client.post(
        f"/v1/platform/support-sessions/{session['id']}/end",
        headers={**beat_cookie, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert ended.status_code == 200, ended.text
    assert client.get(ME, headers=beat_cookie).json() == {"session": None}

    second = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    assert client.get(ME, headers=beat_cookie).json()["session"]["id"] == second["id"]
    db_session.execute(
        update(SupportSession)
        .where(SupportSession.id == uuid.UUID(second["id"]))
        .values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
    )
    db_session.commit()
    assert client.get(ME, headers=beat_cookie).json() == {"session": None}


def test_the_staff_member_acting_for_the_user_reads_the_same_session(client, scene):
    """PR 3 mints the token; here the principal carries `acting_for` by override."""

    session = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=uuid.UUID(scene["staff"]["id"]),
        tenant_id=uuid.UUID(scene["home"]),
        group_id=uuid.uuid4(),
        memberships=frozenset({uuid.UUID(scene["home"])}),
        active_location_id=None,
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=False,
        amr=frozenset({"pwd", "otp", "mfa"}),
        mfa=True,
        acting_for=uuid.UUID(scene["beat"]["id"]),
    )
    try:
        banner = client.get(ME).json()["session"]
    finally:
        app.dependency_overrides.pop(get_current_principal, None)
    assert banner["id"] == session["id"] and banner["viewer"] == "staff"
    assert banner["targetUserName"] == "Beat Verkauf"


# --- the log, newest first ----------------------------------------------------------


def test_the_logs_page_newest_first_with_sort(client, scene, login, db_session):
    staff_cookie = _staff_headers(scene["staff"], tenant_id=scene["home"])
    ids = []
    for _ in range(3):
        session = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
        ids.append(session["id"])
        client.post(
            f"/v1/platform/support-sessions/{session['id']}/end",
            headers={**staff_cookie, "Idempotency-Key": str(uuid.uuid4())},
        )
    # Distinct start times, oldest first in `ids`.
    for offset, session_id in enumerate(ids):
        db_session.execute(
            update(SupportSession)
            .where(SupportSession.id == uuid.UUID(session_id))
            .values(started_at=dt.datetime(2026, 11, 1, 9, tzinfo=dt.UTC) + dt.timedelta(hours=offset))
        )
    db_session.commit()

    manager = cookie_headers(login.session_cookie(scene["manager"]))
    first = client.get(
        f"/v1/dealerships/{scene['home']}/support-sessions",
        params={"sort": "startedAt:desc", "limit": 2},
        headers=manager,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert [e["id"] for e in body["items"]] == [ids[2], ids[1]]
    second = client.get(
        f"/v1/dealerships/{scene['home']}/support-sessions",
        params={"sort": "startedAt:desc", "limit": 2, "cursor": body["nextCursor"]},
        headers=manager,
    ).json()
    assert [e["id"] for e in second["items"]] == [ids[0]] and second["nextCursor"] is None

    platform = client.get(
        "/v1/platform/support-sessions", params={"sort": "startedAt:desc"}, headers=_staff_headers(scene["staff"])
    ).json()
    assert [e["id"] for e in platform["items"]] == list(reversed(ids))


def test_an_unknown_sort_field_is_422(client, scene):
    response = client.get(
        "/v1/platform/support-sessions", params={"sort": "staffName:asc"}, headers=_staff_headers(scene["staff"])
    )
    assert response.status_code == 422, response.text


# --- the Platform users panel -------------------------------------------------------


def test_the_panel_lists_staff_and_legacy_holders_with_factor_and_running_session(client, scene, db_session):
    legacy = create_legacy_staff(client, scene["home"], firstName="Alt", lastName="Konto")
    session = _start(client, scene["staff"], scene["home"], scene["beat"]["id"])
    response = client.get(STAFF, headers=_staff_headers(scene["staff"]))
    assert response.status_code == 200, response.text
    items = {e["id"]: e for e in response.json()["items"]}
    assert set(items) == {scene["staff"]["id"], legacy["id"]}  # never a dealer user
    petra = items[scene["staff"]["id"]]
    assert petra["inPlatformTenant"] is True and petra["homeDealershipId"] == str(PLATFORM_DEALERSHIP_ID)
    assert petra["runningSupportSessionId"] == session["id"]
    assert petra["mfaEnrolled"] is False
    old = items[legacy["id"]]
    assert old["inPlatformTenant"] is False and old["homeDealershipName"] == "Garage Musterbetrieb AG"
    assert old["runningSupportSessionId"] is None


def test_the_panel_is_staff_only(client, scene, login):
    response = client.get(STAFF, headers=cookie_headers(login.session_cookie(scene["manager"])))
    assert response.status_code == 403, response.text


def test_grant_creates_a_staff_account_in_the_platform_tenant_and_invites_it(client, scene, mailbox, db_session):
    response = client.post(
        STAFF,
        json={"firstName": "Neu", "lastName": "Staff", "email": "neu.staff@nexotec.example"},
        headers={**_staff_headers(scene["staff"]), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["inPlatformTenant"] is True and entry["status"] == "invited"
    user = db_session.get(User, uuid.UUID(entry["id"]))
    assert user.tenant_id == PLATFORM_DEALERSHIP_ID and user.access_roles == ["platform_admin"]
    assert any(mail.to == "neu.staff@nexotec.example" for mail in mailbox)


def test_grant_and_revoke_need_a_second_factor(client, scene):
    headers = {**_staff_headers(scene["staff"], mfa=False), "Idempotency-Key": str(uuid.uuid4())}
    response = client.post(
        STAFF, json={"firstName": "A", "lastName": "B", "email": "a.b@nexotec.example"}, headers=headers
    )
    assert response.status_code == 403, response.text
    other = create_staff(client, firstName="Marco", lastName="Hilfe")
    response = client.post(
        f"{STAFF}/{other['id']}/revoke",
        json={"reason": "Austritt"},
        headers={**headers, "If-Match": str(other["version"])},
    )
    assert response.status_code == 403, response.text


def _activate(db_session, *user_ids: str) -> None:
    db_session.execute(
        update(User).where(User.id.in_([uuid.UUID(i) for i in user_ids])).values(status=UserStatus.ACTIVE)
    )
    db_session.commit()


def _version(db_session, user_id: str) -> int:
    db_session.expire_all()
    return db_session.get(User, uuid.UUID(user_id)).version


def _revoke(client, actor, target_id, version, reason="Austritt per Ende Monat"):
    return client.post(
        f"{STAFF}/{target_id}/revoke",
        json={"reason": reason},
        headers={**_staff_headers(actor), "If-Match": str(version), "Idempotency-Key": str(uuid.uuid4())},
    )


def test_revoke_deactivates_a_staff_account_and_audits_the_reason(client, scene, db_session):
    other = create_staff(client, firstName="Marco", lastName="Hilfe")
    _activate(db_session, scene["staff"]["id"], other["id"])
    response = _revoke(client, scene["staff"], other["id"], _version(db_session, other["id"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deactivated"] is True and body["entry"]["status"] == "deactivated"
    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == uuid.UUID(other["id"]), AuditEvent.reason == "Austritt per Ende Monat"
        )
    ).all()
    assert audit and audit[-1].actor_id == uuid.UUID(scene["staff"]["id"])


def test_revoke_removes_only_the_role_from_a_legacy_holder(client, scene, db_session):
    legacy = create_legacy_staff(client, scene["home"], accessRoles=["sales"])
    db_session.execute(
        update(User).where(User.id == uuid.UUID(legacy["id"])).values(access_roles=["sales", "platform_admin"])
    )
    db_session.commit()
    _activate(db_session, scene["staff"]["id"], legacy["id"])
    response = _revoke(client, scene["staff"], legacy["id"], _version(db_session, legacy["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["deactivated"] is False
    db_session.expire_all()
    user = db_session.get(User, uuid.UUID(legacy["id"]))
    assert user.access_roles == ["sales"] and user.status == UserStatus.ACTIVE


def test_a_legacy_holder_whose_only_role_it_is_is_refused(client, scene, db_session):
    legacy = create_legacy_staff(client, scene["home"])
    _activate(db_session, scene["staff"]["id"], legacy["id"])
    response = _revoke(client, scene["staff"], legacy["id"], _version(db_session, legacy["id"]))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "staff_role_is_the_only_role"


def test_never_oneself(client, scene, db_session):
    other = create_staff(client, firstName="Marco", lastName="Hilfe")
    _activate(db_session, scene["staff"]["id"], other["id"])
    response = _revoke(client, scene["staff"], scene["staff"]["id"], _version(db_session, scene["staff"]["id"]))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "platform_admin_self_revoke"


def test_never_the_last_active_holder(client, scene, db_session):
    """Petra (active) revokes Marco while Marco is the only other holder and
    only Marco is active: Nexotec would be left without active staff."""

    other = create_staff(client, firstName="Marco", lastName="Hilfe")
    _activate(db_session, other["id"])  # Petra stays invited: not an active holder
    response = _revoke(client, scene["staff"], other["id"], _version(db_session, other["id"]))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "last_platform_admin"


def test_revoke_of_a_dealer_user_is_404(client, scene, db_session):
    _activate(db_session, scene["staff"]["id"])
    response = _revoke(client, scene["staff"], scene["beat"]["id"], _version(db_session, scene["beat"]["id"]))
    assert response.status_code == 404, response.text


def test_revoke_with_a_stale_version_is_409(client, scene, db_session):
    other = create_staff(client, firstName="Marco", lastName="Hilfe")
    _activate(db_session, scene["staff"]["id"], other["id"])
    response = _revoke(client, scene["staff"], other["id"], _version(db_session, other["id"]) - 1)
    assert response.status_code in (409, 412), response.text


# --- a session still owing a second factor sees and ends the session ----------------


def test_a_session_owing_a_factor_reads_and_ends_the_running_session(client, login):
    """D-A-36 confines a session that owes a factor to the enrolment; the
    banner's read and the ending stay open to it (FR-A-18: the user ends it
    from every screen they can reach). Everything else stays 403."""

    enforced = create_dealership(client, mfa_enforced=True)
    staff = create_staff(client, firstName="Petra", lastName="Support")
    anna = create_user(client, enforced, isDealerManager=True)
    login.accept_invitation(anna)
    cookie = cookie_headers(login.session_cookie(anna))
    assert client.get("/v1/auth/me", headers=cookie).json()["mfaEnrolmentRequired"] is True
    assert client.get("/v1/me/notifications/unread-count", headers=cookie).status_code == 403

    session = _start(client, staff, enforced, anna["id"])
    read = client.get(ME, headers=cookie)
    assert read.status_code == 200, read.text
    assert read.json()["session"]["id"] == session["id"]
    ended = client.post(
        f"/v1/platform/support-sessions/{session['id']}/end",
        headers={**cookie, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["endedBy"] == "user"
    # Confined everywhere else, the manager's own log included.
    assert client.get(f"/v1/dealerships/{enforced}/support-sessions", headers=cookie).status_code == 403
