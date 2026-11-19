"""KAN-221 PR-5 — the support-session log (FR-A-15, D-A-05, D-A-24; the PRD's
matrix row «Support-session log»: the manager for their own Betrieb,
platform_admin across all, written by nobody). Every start and end is also an
entry in the Betrieb's own audit log («every session is listed in the
dealership's own audit log»)."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from app.platform.identity.models.support_session import SupportSession
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)
from tests.platform_tenant_support import create_staff

REASON = "Die Offerte druckt das Logo nicht — Ticket 4712."


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _staff_headers(staff: dict, dealership: str) -> dict[str, str]:
    return platform_admin_headers(tenant_id=uuid.UUID(dealership), user_id=uuid.UUID(staff["id"]))


def _start(client, staff, dealership, target_user_id) -> dict:
    response = client.post(
        "/v1/platform/support-sessions",
        json={"targetUserId": target_user_id, "reason": REASON},
        headers={**_staff_headers(staff, dealership), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _end(client, headers, session_id) -> None:
    response = client.post(
        f"/v1/platform/support-sessions/{session_id}/end",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text


@pytest.fixture()
def scene(client, login):
    """Two Betriebe, a manager and a user in each, two staff members; one
    session per Betrieb, the first ended by its user."""

    home = create_dealership(client)
    other = create_dealership(client, legalName="Autohaus Nebenan AG")
    staff = create_staff(client, firstName="Petra", lastName="Support")
    other_staff = create_staff(client, firstName="Marco", lastName="Hilfe")
    manager = create_user(client, home, firstName="Maria", lastName="Chefin")
    beat = create_user(client, home, firstName="Beat", lastName="Verkauf", isDealerManager=False)
    other_manager = create_user(client, other)
    other_user = create_user(client, other, isDealerManager=False)
    for user in (manager, beat, other_manager, other_user):
        login.accept_invitation(user)

    ended = _start(client, staff, home, beat["id"])
    _end(client, cookie_headers(login.session_cookie(beat)), ended["id"])
    running = _start(client, other_staff, other, other_user["id"])
    return {
        "home": home,
        "other": other,
        "staff": staff,
        "other_staff": other_staff,
        "manager": manager,
        "beat": beat,
        "other_manager": other_manager,
        "ended": ended,
        "running": running,
    }


def test_the_manager_reads_the_own_betriebs_sessions_with_names_and_how_each_ended(client, scene, login):
    response = client.get(
        f"/v1/dealerships/{scene['home']}/support-sessions",
        headers=cookie_headers(login.session_cookie(scene["manager"])),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nextCursor"] is None
    [entry] = body["items"]
    assert entry["id"] == scene["ended"]["id"]
    assert entry["staffUserId"] == scene["staff"]["id"] and entry["staffName"] == "Petra Support"
    assert entry["targetUserId"] == scene["beat"]["id"] and entry["targetUserName"] == "Beat Verkauf"
    assert entry["dealershipName"] == "Garage Musterbetrieb AG"
    assert entry["reason"] == REASON
    assert entry["endedBy"] == "user" and entry["active"] is False


def test_another_betriebs_log_is_404_never_403(client, scene, login):
    response = client.get(
        f"/v1/dealerships/{scene['other']}/support-sessions",
        headers=cookie_headers(login.session_cookie(scene["manager"])),
    )
    assert response.status_code == 404, response.text


def test_a_user_without_the_manager_flag_may_not_read_the_log(client, scene, login):
    response = client.get(
        f"/v1/dealerships/{scene['home']}/support-sessions",
        headers=cookie_headers(login.session_cookie(scene["beat"])),
    )
    assert response.status_code == 403, response.text


def test_a_timed_out_session_reads_as_timeout_in_the_log(client, scene, login, db_session):
    db_session.execute(
        update(SupportSession)
        .where(SupportSession.id == uuid.UUID(scene["running"]["id"]))
        .values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
    )
    db_session.commit()
    response = client.get(
        f"/v1/dealerships/{scene['other']}/support-sessions",
        headers=cookie_headers(login.session_cookie(scene["other_manager"])),
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["items"]
    assert entry["endedBy"] == "timeout" and entry["active"] is False


def test_staff_read_every_betriebs_sessions_and_narrow_them(client, scene):
    headers = platform_admin_headers()
    everything = client.get("/v1/platform/support-sessions", headers=headers)
    assert everything.status_code == 200, everything.text
    assert [e["id"] for e in everything.json()["items"]] == [scene["ended"]["id"], scene["running"]["id"]]
    running = everything.json()["items"][1]
    assert running["active"] is True and running["endedBy"] is None
    assert running["dealershipName"] == "Autohaus Nebenan AG" and running["staffName"] == "Marco Hilfe"

    by_betrieb = client.get(f"/v1/platform/support-sessions?dealershipId={scene['other']}", headers=headers)
    assert [e["id"] for e in by_betrieb.json()["items"]] == [scene["running"]["id"]]
    by_staff = client.get(f"/v1/platform/support-sessions?staffUserId={scene['staff']['id']}", headers=headers)
    assert [e["id"] for e in by_staff.json()["items"]] == [scene["ended"]["id"]]


def test_the_platform_wide_log_is_staff_only(client, scene, login):
    response = client.get(
        "/v1/platform/support-sessions", headers=cookie_headers(login.session_cookie(scene["manager"]))
    )
    assert response.status_code == 403, response.text


def test_the_log_pages_by_cursor(client, scene):
    headers = platform_admin_headers()
    first = client.get("/v1/platform/support-sessions?limit=1", headers=headers).json()
    assert [e["id"] for e in first["items"]] == [scene["ended"]["id"]] and first["nextCursor"]
    second = client.get(f"/v1/platform/support-sessions?limit=1&cursor={first['nextCursor']}", headers=headers).json()
    assert [e["id"] for e in second["items"]] == [scene["running"]["id"]] and second["nextCursor"] is None


def test_every_start_and_end_is_in_the_betriebs_audit_log(client, scene, login):
    response = client.get(
        f"/v1/dealerships/{scene['home']}/audit-log?limit=100",
        headers=cookie_headers(login.session_cookie(scene["manager"])),
    )
    assert response.status_code == 200, response.text
    rows = [e for e in response.json()["items"] if e["entityType"] == "support_session"]
    assert [(e["entityId"], e["action"]) for e in rows] == [
        (scene["ended"]["id"], "start"),
        (scene["ended"]["id"], "end"),
    ]
    assert all(e["actorId"] != scene["manager"]["id"] for e in rows)
