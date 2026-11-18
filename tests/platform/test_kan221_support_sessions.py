"""KAN-221 PR-2 — the support session's record (D-A-05, D-A-24, FR-A-15,
FR-A-17, FR-A-18): platform staff with a second factor start one for an
active user with a stated reason; 60 minutes; never for a platform_admin;
the staff member or the user ends it; anyone else is told it does not
exist; every start and end is audited on the user's Betrieb with the staff
member as actor. The token that acts for the user is PR 3."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.base import utcnow
from app.main import app
from app.platform.administration.services.data_export_tables import PlatformExporter
from app.platform.daily_jobs import run_daily_support_session_expiry
from app.platform.identity.models.support_session import SupportSession
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)
from tests.platform_tenant_support import create_staff

REASON = "Der Betrieb meldet, dass die Offerte nicht gedruckt wird — Ticket 4711."


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


@pytest.fixture()
def scene(client, login):
    dealership = create_dealership(client)
    staff = create_staff(client)
    beat = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(beat)
    return dealership, staff, beat


def _staff_headers(staff: dict, dealership: str, *, mfa: bool = True) -> dict[str, str]:
    return platform_admin_headers(tenant_id=uuid.UUID(dealership), user_id=uuid.UUID(staff["id"]), mfa=mfa)


def _start(client, headers, target_user_id, reason=REASON):
    return client.post(
        "/v1/platform/support-sessions",
        json={"targetUserId": target_user_id, "reason": reason},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


def _end(client, headers, session_id):
    return client.post(
        f"/v1/platform/support-sessions/{session_id}/end",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


def test_staff_start_a_sixty_minute_session_for_an_active_user_and_it_is_audited(client, scene, db_session):
    dealership, staff, beat = scene
    before = utcnow()
    response = _start(client, _staff_headers(staff, dealership), beat["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["staffUserId"] == staff["id"] and body["targetUserId"] == beat["id"]
    assert body["dealershipId"] == dealership
    assert body["reason"] == REASON
    assert body["active"] is True and body["endedAt"] is None and body["endedBy"] is None
    started = dt.datetime.fromisoformat(body["startedAt"])
    assert started >= before - dt.timedelta(seconds=1)
    assert dt.datetime.fromisoformat(body["expiresAt"]) - started == dt.timedelta(minutes=60)

    event = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(body["id"]))).one()
    assert event.entity_type == "support_session" and event.action == "start"
    # The staff member is the actor, never the dealer; the row is the Betrieb's.
    assert event.actor_id == uuid.UUID(staff["id"])
    assert event.tenant_id == uuid.UUID(dealership)
    assert event.after["targetUserId"] == beat["id"] and event.after["reason"] == REASON


def test_only_platform_staff_with_a_second_factor_start_one(client, scene, login, db_session):
    dealership, staff, beat = scene
    manager = create_user(client, dealership)
    login.accept_invitation(manager)
    for headers, message in (
        (cookie_headers(login.session_cookie(manager)), "platform staff"),
        (_staff_headers(staff, dealership, mfa=False), "second factor"),
    ):
        refused = _start(client, headers, beat["id"])
        assert refused.status_code == 403, refused.text
        assert message in refused.json()["error"]["message"]
    assert db_session.scalars(select(SupportSession)).all() == []


def test_no_session_is_started_from_inside_an_impersonation(client, scene):
    dealership, staff, beat = scene
    acting = Principal(
        user_id=uuid.UUID(staff["id"]),
        tenant_id=uuid.UUID(dealership),
        group_id=uuid.uuid4(),
        memberships=frozenset({uuid.UUID(dealership)}),
        active_location_id=None,
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        is_dealer_manager=False,
        amr=frozenset({"pwd", "otp", "mfa"}),
        mfa=True,
        acting_for=uuid.uuid4(),
    )
    app.dependency_overrides[get_current_principal] = lambda: acting
    try:
        refused = client.post(
            "/v1/platform/support-sessions",
            json={"targetUserId": beat["id"], "reason": REASON},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
    finally:
        app.dependency_overrides.pop(get_current_principal, None)
    assert refused.status_code == 403, refused.text


def test_a_reason_is_mandatory(client, scene):
    dealership, staff, beat = scene
    for reason in ("", "   "):
        refused = _start(client, _staff_headers(staff, dealership), beat["id"], reason=reason)
        assert refused.status_code == 422, refused.text


def test_refused_targets(client, scene):
    """FR-A-17: a platform_admin cannot be impersonated; nor oneself, nor a
    user who is not active; an unknown user is 404."""

    dealership, staff, _beat = scene
    headers = _staff_headers(staff, dealership)
    colleague = create_staff(client)
    invited = create_user(client, dealership, isDealerManager=False)
    for target, reason in (
        (colleague["id"], "support_target_platform_admin"),
        (staff["id"], "support_target_self"),
        (invited["id"], "support_target_not_active"),
    ):
        refused = _start(client, headers, target)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["details"]["reason"] == reason
    assert _start(client, headers, str(uuid.uuid4())).status_code == 404


def test_one_running_session_at_a_time_and_an_expired_one_closes_as_timeout(client, scene, login, db_session):
    dealership, staff, beat = scene
    headers = _staff_headers(staff, dealership)
    first = _start(client, headers, beat["id"]).json()
    second_user = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(second_user)

    refused = _start(client, headers, second_user["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "support_session_already_open"

    # The first runs out.
    row = db_session.get(SupportSession, uuid.UUID(first["id"]))
    row.started_at = utcnow() - dt.timedelta(minutes=61)
    row.expires_at = utcnow() - dt.timedelta(minutes=1)
    db_session.commit()
    expires_at = row.expires_at

    started = _start(client, headers, second_user["id"])
    assert started.status_code == 201, started.text
    db_session.expire_all()
    closed = db_session.get(SupportSession, uuid.UUID(first["id"]))
    assert closed.ended_by is not None and closed.ended_by.value == "timeout"
    assert closed.ended_at == expires_at
    ends = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == closed.id, AuditEvent.action == "end")
    ).all()
    # Nobody ended it: a system write, never the staff member as actor.
    assert [(e.after["endedBy"], e.actor_id, e.reason) for e in ends] == [
        ("timeout", None, "support_session_timed_out")
    ]


def test_the_user_ends_it(client, scene, login, db_session):
    dealership, staff, beat = scene
    session = _start(client, _staff_headers(staff, dealership), beat["id"]).json()
    ended = _end(client, cookie_headers(login.session_cookie(beat)), session["id"])
    assert ended.status_code == 200, ended.text
    assert ended.json()["endedBy"] == "user" and ended.json()["active"] is False
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(session["id"]), AuditEvent.action == "end")
    ).one()
    assert event.actor_id == uuid.UUID(beat["id"]) and event.after["endedBy"] == "user"


def test_the_staff_member_ends_it_and_an_ended_session_cannot_end_again(client, scene):
    dealership, staff, beat = scene
    headers = _staff_headers(staff, dealership)
    session = _start(client, headers, beat["id"]).json()
    ended = _end(client, headers, session["id"])
    assert ended.status_code == 200, ended.text
    assert ended.json()["endedBy"] == "staff"
    again = _end(client, headers, session["id"])
    assert again.status_code == 409, again.text
    assert again.json()["error"]["details"] == {"reason": "support_session_ended", "endedBy": "staff"}
    # A new one may start once the first has ended.
    assert _start(client, headers, beat["id"]).status_code == 201


def test_an_expired_session_reads_as_timed_out_and_cannot_be_ended(client, scene, db_session):
    dealership, staff, beat = scene
    headers = _staff_headers(staff, dealership)
    session = _start(client, headers, beat["id"]).json()
    row = db_session.get(SupportSession, uuid.UUID(session["id"]))
    row.expires_at = utcnow() - dt.timedelta(seconds=1)
    db_session.commit()
    refused = _end(client, headers, session["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["endedBy"] == "timeout"


def test_nobody_else_learns_the_session_exists(client, scene, login):
    dealership, staff, beat = scene
    session = _start(client, _staff_headers(staff, dealership), beat["id"]).json()
    other_staff = create_staff(client)
    manager = create_user(client, dealership)
    login.accept_invitation(manager)
    for headers in (_staff_headers(other_staff, dealership), cookie_headers(login.session_cookie(manager))):
        refused = _end(client, headers, session["id"])
        assert refused.status_code == 404, refused.text
    assert _end(client, _staff_headers(staff, dealership), str(uuid.uuid4())).status_code == 404


def test_the_betriebs_export_carries_its_support_sessions():
    tables = {exported.table.name for exported in PlatformExporter().exported_tables()}
    assert "support_session" in tables


def test_the_nightly_job_writes_a_timed_out_ending_into_the_row(client, scene, db_session):
    """`platform.support_sessions.close_expired`: a session nobody ended is
    closed at its `expires_at` by `timeout`, audited as a system write; a
    running one and an ended one are left alone."""

    dealership, staff, beat = scene
    headers = _staff_headers(staff, dealership)
    ran_out = _start(client, headers, beat["id"]).json()
    row = db_session.get(SupportSession, uuid.UUID(ran_out["id"]))
    row.expires_at = utcnow() - dt.timedelta(minutes=5)
    db_session.commit()
    expires_at = row.expires_at
    other_staff = create_staff(client)
    running = _start(client, _staff_headers(other_staff, dealership), beat["id"]).json()
    third_staff = create_staff(client)
    ended = _start(client, _staff_headers(third_staff, dealership), beat["id"]).json()
    assert _end(client, _staff_headers(third_staff, dealership), ended["id"]).status_code == 200
    ended_row = db_session.get(SupportSession, uuid.UUID(ended["id"]))
    ended_row.expires_at = utcnow() - dt.timedelta(minutes=1)  # past its end, but ended by staff
    db_session.commit()

    run_daily_support_session_expiry(db_session)

    db_session.expire_all()
    closed = db_session.get(SupportSession, uuid.UUID(ran_out["id"]))
    assert closed.ended_by is not None and closed.ended_by.value == "timeout" and closed.ended_at == expires_at
    still = db_session.get(SupportSession, uuid.UUID(running["id"]))
    assert still.ended_at is None
    by_staff = db_session.get(SupportSession, uuid.UUID(ended["id"]))
    assert by_staff.ended_by is not None and by_staff.ended_by.value == "staff"
    timed_out = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "support_session", AuditEvent.reason == "support_session_timed_out")
    ).all()
    assert [(e.entity_id, e.actor_id) for e in timed_out] == [(closed.id, None)]
    # A second run finds nothing more.
    run_daily_support_session_expiry(db_session)
    assert len(db_session.scalars(select(AuditEvent).where(AuditEvent.action == "end")).all()) == 2
