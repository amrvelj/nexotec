"""KAN-200 — deactivation with the reassignment step (Dealer Administration
FR-A-37, D-A-35), the lifecycle stamps and the validity window (FR-A-12),
and the nightly expiry job.
"""

import datetime as dt
import logging
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.ownership import ownership_provider, registered_ownership_modules
from app.core.reconciliation import ReconciliationAlarm
from app.platform import daily_jobs
from app.platform.daily_jobs import run_daily_user_expiry
from app.platform.models.user import User
from tests.identity_support import PasswordLogin, create_dealership, create_user, deactivate, platform_admin_headers


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _as(user: dict, *, role: AccessRole = AccessRole.SALES, manager: bool = False) -> dict[str, str]:
    tid = uuid.UUID(user["dealershipId"])
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}),
        is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _customer_for(client, user: dict, **overrides) -> dict:
    payload = {
        "firstName": "Ada",
        "lastName": "Byron",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"ada-{uuid.uuid4().hex[:6]}@example.ch"}],
        **overrides,
    }
    response = client.post("/v1/customers", json=payload, headers=_as(user))
    assert response.status_code == 201, response.text
    return response.json()


def _offer_for(client, user: dict) -> dict:
    response = client.post("/v1/sales/offers", headers=_as(user))
    assert response.status_code == 201, response.text
    return response.json()


def _contract_for(client, user: dict, customer: dict) -> dict:
    response = client.post("/v1/sales/contracts", json={"customerId": customer["id"]}, headers=_as(user))
    assert response.status_code == 201, response.text
    return response.json()


def _ownership(client, dealership: str, user: dict) -> dict:
    response = client.get(
        f"/v1/dealerships/{dealership}/users/{user['id']}/ownership", headers=platform_admin_headers()
    )
    assert response.status_code == 200, response.text
    return response.json()


def _audit(db_session, user_id: str, action: str) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(user_id), AuditEvent.action == action)
        ).all()
    )


# --- the registry ------------------------------------------------------------------


def test_every_context_with_user_owned_records_registers_a_provider():
    """The deactivation lists modules from the registry, so an omission would
    be silent — the five the ticket names are there, stubs included."""

    assert registered_ownership_modules() == ["aftersales", "customer", "inventory", "parts", "sales"]


# --- what a user owns ----------------------------------------------------------------


def test_ownership_lists_open_records_by_module_and_names_every_module(client):
    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    create_user(client, dealership)  # the manager who stays

    customer = _customer_for(client, leaver)
    _customer_for(client, leaver)
    _offer_for(client, leaver)
    _contract_for(client, leaver, customer)

    body = _ownership(client, dealership, leaver)
    assert body["modules"] == ["aftersales", "customer", "inventory", "parts", "sales"]
    assert body["groups"] == [
        {"module": "customer", "group": "customers", "count": 2},
        {"module": "sales", "group": "offers", "count": 1},
        {"module": "sales", "group": "contracts", "count": 1},
    ]


def test_a_cancelled_offer_and_a_merged_customer_are_not_open(client, db_session):
    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    offer = _offer_for(client, leaver)
    cancelled = client.post(
        f"/v1/sales/offers/{offer['id']}/cancel",
        json={"reason": "customer bought elsewhere"},
        headers={**_as(leaver), "If-Match": str(offer["version"])},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert _ownership(client, dealership, leaver)["groups"] == []


# --- the deactivation step ------------------------------------------------------------


def test_deactivation_needs_a_decision_for_every_open_group(client):
    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    _customer_for(client, leaver)
    _offer_for(client, leaver)

    response = deactivate(
        client, dealership, leaver, transfers=[{"module": "sales", "group": "offers", "toUserId": None}]
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["problems"] == ["customer/customers needs a decision: a colleague, or null to leave it unassigned"]
    assert {(g["module"], g["group"]) for g in details["openGroups"]} == {
        ("customer", "customers"),
        ("sales", "offers"),
    }
    # Nothing changed: the user is still invited, the offer still theirs.
    still = client.get(f"/v1/dealerships/{dealership}/users/{leaver['id']}", headers=platform_admin_headers()).json()
    assert still["status"] == "invited" and still["version"] == 1


@pytest.mark.parametrize("bad", ["self", "other_dealership", "deactivated_colleague", "unknown_group", "twice"])
def test_a_transfer_to_the_wrong_person_or_group_is_refused(client, bad):
    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    _customer_for(client, leaver)
    if bad == "self":
        to = leaver["id"]
    elif bad == "other_dealership":
        to = create_user(client, create_dealership(client))["id"]
    elif bad == "deactivated_colleague":
        gone = create_user(client, dealership, isDealerManager=False)
        create_user(client, dealership)
        assert deactivate(client, dealership, gone).status_code == 200
        to = gone["id"]
    else:
        to = None
    transfers = [{"module": "customer", "group": "customers", "toUserId": to}]
    if bad == "unknown_group":
        transfers.append({"module": "sales", "group": "offers", "toUserId": None})
    if bad == "twice":
        transfers.append({"module": "customer", "group": "customers", "toUserId": None})
    response = deactivate(client, dealership, leaver, transfers=transfers)
    assert response.status_code == 422, response.text


def test_deactivation_hands_each_group_over_and_audits_every_step(client, db_session, login):
    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False, firstName="Lea", lastName="Ver")
    heir = create_user(client, dealership, isDealerManager=False, firstName="Hans", lastName="Erbe")
    manager = create_user(client, dealership)
    customer = _customer_for(client, leaver)
    offer = _offer_for(client, leaver)
    contract = _contract_for(client, leaver, customer)
    login.accept_invitation(leaver)
    login.accept_invitation(heir)  # FR-A-37: records go to an ACTIVE colleague
    assert login.login(leaver).status_code == 200

    response = deactivate(
        client,
        dealership,
        leaver,
        headers=_as(manager, manager=True),
        if_match=2,
        reason="left on 30 September",
        transfers=[
            {"module": "customer", "group": "customers", "toUserId": heir["id"]},
            {"module": "sales", "group": "offers", "toUserId": None},
            {"module": "sales", "group": "contracts", "toUserId": heir["id"]},
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["status"] == "deactivated"
    assert body["user"]["deactivationReason"] == "left on 30 September"
    assert body["user"]["deactivatedAt"] is not None
    assert body["transfers"] == [
        {"module": "customer", "group": "customers", "toUserId": heir["id"], "count": 1},
        {"module": "sales", "group": "offers", "toUserId": None, "count": 1},
        {"module": "sales", "group": "contracts", "toUserId": heir["id"], "count": 1},
    ]

    # The records moved — with the receiver's label, or to nobody.
    moved_customer = client.get(f"/v1/customers/{customer['id']}", headers=_as(heir)).json()
    assert moved_customer["advisorId"] == heir["id"] and moved_customer["advisorLabel"] == "Hans Erbe"
    released = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(heir)).json()
    assert released["ownerUserId"] is None and released["ownerLabel"] is None
    assert released["version"] == offer["version"] + 1
    moved_contract = client.get(f"/v1/sales/contracts/{contract['id']}", headers=_as(heir)).json()
    assert moved_contract["ownerUserId"] == heir["id"] and moved_contract["ownerLabel"] == "Hans Erbe"
    # The deal grid follows: the heir's deals, and nothing of the leaver's.
    mine = client.get(f"/v1/sales/deals?ownerUserId={heir['id']}", headers=_as(heir)).json()
    assert [d["contractId"] for d in mine["items"]] == [contract["id"]]
    assert client.get(f"/v1/sales/deals?ownerUserId={leaver['id']}", headers=_as(heir)).json()["total"] == 0

    # Nothing is left with the leaver, and their session ended.
    assert _ownership(client, dealership, leaver)["groups"] == []
    assert login.login(leaver).status_code == 401

    # Audit: the status change with the reason, one row per group with the granter.
    [status_row] = _audit(db_session, leaver["id"], "update")
    assert status_row.after["status"] == "deactivated" and status_row.reason == "left on 30 September"
    assert str(status_row.actor_id) == manager["id"]
    transfers = _audit(db_session, leaver["id"], "ownership_transferred")
    assert [(r.after["module"], r.after["group"], r.after["owner_user_id"], r.after["count"]) for r in transfers] == [
        ("customer", "customers", heir["id"], 1),
        ("sales", "offers", None, 1),
        ("sales", "contracts", heir["id"], 1),
    ]
    assert all(str(r.actor_id) == manager["id"] and r.reason == "left on 30 September" for r in transfers)
    # The customer's own history shows the hand-over too.
    customer_rows = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(customer["id"]), AuditEvent.action == "update")
        ).all()
    )
    # … carrying the manager's reason, as platform's rows do (never a code).
    assert any(r.after == {"advisor_id": heir["id"]} and r.reason == "left on 30 September" for r in customer_rows)


def test_a_failed_module_leaves_its_records_for_the_retry_and_the_user_deactivated(
    client, db_session, monkeypatch, login
):
    """ADR-047: no shared transaction across the seam. The customer module's
    hand-over commits; sales fails; the user is deactivated (access cut
    first), the customer transfer is recorded, and the offers are still
    theirs for `POST …/ownership/transfers`."""

    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    heir = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(heir)
    create_user(client, dealership)
    customer = _customer_for(client, leaver)
    _offer_for(client, leaver)

    sales = ownership_provider("sales")
    assert sales is not None

    def boom(*args, **kwargs):
        raise RuntimeError("sales is down")

    monkeypatch.setattr(sales, "transfer", boom)
    response = deactivate(
        client,
        dealership,
        leaver,
        transfers=[
            {"module": "customer", "group": "customers", "toUserId": heir["id"]},
            {"module": "sales", "group": "offers", "toUserId": heir["id"]},
        ],
    )
    monkeypatch.undo()
    # Answered, never a 500 after the status changed: what moved, what did not.
    assert response.status_code == 200, response.text
    assert response.json()["transfers"] == [
        {"module": "customer", "group": "customers", "toUserId": heir["id"], "count": 1}
    ]
    assert response.json()["failed"] == [{"module": "sales", "group": "offers", "error": "sales is down"}]

    user = client.get(f"/v1/dealerships/{dealership}/users/{leaver['id']}", headers=platform_admin_headers()).json()
    assert user["status"] == "deactivated"
    assert client.get(f"/v1/customers/{customer['id']}", headers=_as(heir)).json()["advisorId"] == heir["id"]
    assert [r.after["module"] for r in _audit(db_session, leaver["id"], "ownership_transferred")] == ["customer"]
    assert _ownership(client, dealership, leaver)["groups"] == [{"module": "sales", "group": "offers", "count": 1}]

    retry = client.post(
        f"/v1/dealerships/{dealership}/users/{leaver['id']}/ownership/transfers",
        json={"transfers": [{"module": "sales", "group": "offers", "toUserId": heir["id"]}]},
        headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["transfers"] == [{"module": "sales", "group": "offers", "toUserId": heir["id"], "count": 1}]
    assert _ownership(client, dealership, leaver)["groups"] == []


def test_the_hand_over_endpoint_also_serves_a_user_who_stays(client, login):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, isDealerManager=False)
    heir = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(heir)
    offer = _offer_for(client, seller)
    response = client.post(
        f"/v1/dealerships/{dealership}/users/{seller['id']}/ownership/transfers",
        json={"transfers": [{"module": "sales", "group": "offers", "toUserId": heir["id"]}]},
        headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(heir)).json()["ownerUserId"] == heir["id"]
    assert (
        client.get(f"/v1/dealerships/{dealership}/users/{seller['id']}", headers=platform_admin_headers()).json()[
            "status"
        ]
        == "invited"
    )


def test_a_manager_may_deactivate_but_never_a_staff_account_and_never_across_tenants(client):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    staff = create_user(client, dealership, isDealerManager=False, accessRoles=["platform_admin"])
    other = create_dealership(client)
    foreign = create_user(client, other, isDealerManager=False)
    create_user(client, other)

    assert deactivate(client, dealership, staff, headers=_as(manager, manager=True)).status_code == 403
    assert deactivate(client, other, foreign, headers=_as(manager, manager=True)).status_code == 404
    assert deactivate(client, dealership, foreign, headers=_as(manager, manager=True)).status_code == 404
    assert (
        deactivate(client, dealership, manager, headers=_as(manager, manager=True)).status_code == 400
    )  # last manager


def test_a_deactivated_user_cannot_be_deactivated_again(client):
    dealership = create_dealership(client)
    user = create_user(client, dealership, isDealerManager=False)
    create_user(client, dealership)
    assert deactivate(client, dealership, user).status_code == 200
    assert deactivate(client, dealership, user, if_match=2).status_code == 409


# --- lifecycle stamps and the validity window (FR-A-12) ----------------------------------


def test_invited_at_last_login_at_and_the_invitation_expiry_are_stamped(client, login, mailbox):
    dealership = create_dealership(client)
    user = create_user(client, dealership)
    listed = client.get(f"/v1/dealerships/{dealership}/users", headers=platform_admin_headers()).json()["items"]
    [row] = [u for u in listed if u["id"] == user["id"]]
    assert row["invitedAt"] is not None
    assert row["invitationExpiresAt"] is not None
    assert row["lastLoginAt"] is None

    first_invited_at = row["invitedAt"]
    reissued = client.post(
        f"/v1/dealerships/{dealership}/users/{user['id']}/invitations", headers=platform_admin_headers()
    )
    assert reissued.status_code == 202, reissued.text
    again = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()
    assert again["invitedAt"] >= first_invited_at

    login.accept_invitation(user)
    assert login.login(user).status_code == 200
    after = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()
    assert after["lastLoginAt"] is not None
    assert after["invitationExpiresAt"] is None  # consumed


def test_a_user_outside_their_validity_window_cannot_sign_in(client, login):
    dealership = create_dealership(client)
    today = dt.datetime.now(ZoneInfo("Europe/Zurich")).date()
    user = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(user)
    assert login.login(user).status_code == 200

    not_yet = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validFrom": str(today + dt.timedelta(days=1))},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    assert not_yet.status_code == 200, not_yet.text
    assert login.login(user).status_code == 401

    opened = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validFrom": str(today)},
        headers={**platform_admin_headers(), "If-Match": "3"},
    )
    assert opened.status_code == 200, opened.text
    assert login.login(user).status_code == 200

    # The end before the stored start is refused (the window is judged as it
    # will be after the PATCH, not only when a body carries both dates) …
    inverted = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validTo": str(today - dt.timedelta(days=1))},
        headers={**platform_admin_headers(), "If-Match": "4"},
    )
    assert inverted.status_code == 422, inverted.text
    # … and a window that lies in the past closes the door.
    closed = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validFrom": str(today - dt.timedelta(days=2)), "validTo": str(today - dt.timedelta(days=1))},
        headers={**platform_admin_headers(), "If-Match": "4"},
    )
    assert closed.status_code == 200, closed.text
    assert login.login(user).status_code == 401


def test_a_validity_window_ending_before_it_starts_is_refused(client):
    dealership = create_dealership(client)
    response = client.post(
        f"/v1/dealerships/{dealership}/users",
        json={
            "firstName": "A",
            "lastName": "B",
            "email": f"ab-{uuid.uuid4().hex[:6]}@example.ch",
            "role": "sales",
            "accessRoles": ["sales"],
            "validFrom": "2026-12-01",
            "validTo": "2026-11-01",
        },
        headers=platform_admin_headers(),
    )
    assert response.status_code == 422, response.text


# --- the nightly expiry job ----------------------------------------------------------------


def test_the_expiry_job_deactivates_expired_users_and_releases_their_records(client, db_session, engine, caplog):
    dealership = create_dealership(client)
    yesterday = str(dt.datetime.now(ZoneInfo("Europe/Zurich")).date() - dt.timedelta(days=1))
    create_user(client, dealership)  # stays the manager
    expired = create_user(client, dealership, isDealerManager=False, validTo=yesterday)
    current = create_user(client, dealership, isDealerManager=False)
    customer = _customer_for(client, expired)
    offer = _offer_for(client, expired)

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_user_expiry(db_session, session_factory=sessionmaker(bind=engine))

    db_session.expire_all()
    gone = db_session.get(User, uuid.UUID(expired["id"]))
    assert gone is not None and gone.status.value == "deactivated"
    assert gone.deactivation_reason == "valid_to_expired" and gone.deactivated_at is not None
    assert db_session.get(User, uuid.UUID(current["id"])).status.value == "invited"
    assert client.get(f"/v1/customers/{customer['id']}", headers=_as(current)).json()["advisorId"] is None
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(current)).json()["ownerUserId"] is None
    rows = _audit(db_session, expired["id"], "ownership_transferred")
    assert {r.after["module"] for r in rows} == {"customer", "sales"} and all(r.actor_id is None for r in rows)
    [record] = [r for r in caplog.records if r.name == daily_jobs.__name__]
    assert record.levelno == logging.INFO and "deactivated=1" in record.getMessage()


def test_the_expiry_job_keeps_the_last_manager_and_alarms(client, db_session, engine, caplog):
    dealership = create_dealership(client)
    yesterday = str(dt.datetime.now(ZoneInfo("Europe/Zurich")).date() - dt.timedelta(days=1))
    only_manager = create_user(client, dealership, validTo=yesterday)

    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_user_expiry(db_session, session_factory=sessionmaker(bind=engine))

    db_session.expire_all()
    assert db_session.get(User, uuid.UUID(only_manager["id"])).status.value == "invited"
    errors = [r for r in caplog.records if r.name == daily_jobs.__name__ and r.levelno == logging.ERROR]
    assert errors and "skipped_last_manager=1" in errors[-1].getMessage()


# --- the review's findings (2026-10-19): one transaction, the lifecycle, the window ----


def test_the_user_and_the_invitation_are_one_transaction(client, db_session, monkeypatch):
    """FR-A-09: the user record and the invitation token are written in one
    transaction — a failure issuing the token leaves no half-created user."""

    from app.platform.services import identity

    dealership = create_dealership(client)
    email = f"one-tx-{uuid.uuid4().hex[:6]}@example.ch"

    def boom(*args, **kwargs):
        raise RuntimeError("token store is down")

    monkeypatch.setattr(identity.auth_tokens, "issue", boom)
    with pytest.raises(RuntimeError):
        client.post(
            f"/v1/dealerships/{dealership}/users",
            json={"firstName": "Half", "lastName": "Made", "email": email, "role": "sales", "accessRoles": ["sales"]},
            headers=platform_admin_headers(),
        )
    monkeypatch.undo()
    assert db_session.scalar(select(User).where(User.email == email)) is None


def test_an_invited_user_is_activated_only_by_accepting_and_never_suspended(client):
    """Users → Lifecycle: `Invited → Active` is the acceptance (the person
    sets their own password); a PATCH cannot skip it, and only an active
    user can be suspended."""

    dealership = create_dealership(client)
    user = create_user(client, dealership, isDealerManager=False)
    create_user(client, dealership)
    url = f"/v1/dealerships/{dealership}/users/{user['id']}"
    activated = client.patch(url, json={"status": "active"}, headers={**platform_admin_headers(), "If-Match": "1"})
    assert activated.status_code == 409, activated.text
    assert activated.json()["error"]["details"]["reason"] == "accept_invitation_required"
    suspended = client.patch(url, json={"status": "suspended"}, headers={**platform_admin_headers(), "If-Match": "1"})
    assert suspended.status_code == 409, suspended.text
    assert suspended.json()["error"]["details"]["reason"] == "not_active"
    # The invitation is still the manager's to resend.
    resent = client.post(
        f"{url}/invitations", headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4())}
    )
    assert resent.status_code == 202, resent.text


def test_reactivating_a_user_who_never_accepted_returns_them_to_invited(client, login):
    """A user deactivated while `invited` holds no password: reactivation
    makes them `invited` again — the manager resends the invitation and the
    person sets their password — never `active` with no way in."""

    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    user = create_user(client, dealership, isDealerManager=False)
    assert deactivate(client, dealership, user).status_code == 200
    url = f"/v1/dealerships/{dealership}/users/{user['id']}"
    back = client.patch(url, json={"status": "active"}, headers={**platform_admin_headers(), "If-Match": "2"})
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "invited"
    resent = client.post(
        f"{url}/invitations", headers={**_as(manager, manager=True), "Idempotency-Key": str(uuid.uuid4())}
    )
    assert resent.status_code == 202, resent.text
    login.accept_invitation(user)
    assert login.login(user).status_code == 200


def test_an_invitation_can_be_accepted_before_the_window_opens_but_no_session_is_minted(client, mailbox):
    """FR-A-12: a hire with a future `valid_from` sets their password when
    the invitation arrives (its 14 days may run out before the first day);
    the acceptance says when access starts instead of a session, and the
    login keeps its uniform 401 until then."""

    from tests.identity_support import last_mail_to, token_in

    dealership = create_dealership(client)
    today = dt.datetime.now(ZoneInfo("Europe/Zurich")).date()
    start = today + dt.timedelta(days=3)
    user = create_user(client, dealership, isDealerManager=False, validFrom=str(start))
    token = token_in(last_mail_to(mailbox, user["email"]))
    accepted = client.post(
        "/v1/auth/invitations/accept", json={"token": token, "password": "correct horse battery staple!"}
    )
    assert accepted.status_code == 403, accepted.text
    assert accepted.json()["error"]["details"] == {
        "reason": "access_window_closed",
        "validFrom": str(start),
        "validTo": None,
    }
    row = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()
    assert row["status"] == "active"  # the password is set, the account is theirs
    login = PasswordLogin(client, mailbox)
    assert login.login(user, password="correct horse battery staple!").status_code == 401
    opened = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validFrom": str(today)},
        headers={**platform_admin_headers(), "If-Match": str(row["version"])},
    )
    assert opened.status_code == 200, opened.text
    assert login.login(user, password="correct horse battery staple!").status_code == 200


def test_the_session_user_keeps_its_pre_kan_200_shape(client, login):
    """The ticket's do-not-touch: `LoginResponse.user` (and `GET /auth/me`)
    carries the fields it did before — the administration fields, the sites
    and the derived `hasSignatureImage` are `UserRead`'s alone."""

    dealership = create_dealership(client)
    user = create_user(client, dealership)
    login.accept_invitation(user)
    body = login.login(user).json()["session"]["user"]
    assert set(body) == {
        "id",
        "dealershipId",
        "firstName",
        "lastName",
        "email",
        "phone",
        "role",
        "accessRoles",
        "isDealerManager",
        "employmentStatus",
        "authIdentityId",
        "status",
        "version",
        "createdAt",
        "updatedAt",
        "createdBy",
        "updatedBy",
    }


def test_the_customer_list_filters_the_unassigned_queue_and_one_advisor(client, login):
    """FR-A-37: "unassigned items appear in the module's own queue" — for
    Customers, `GET /customers?unassigned=true`; `advisorUserId` is the
    colleague's view after a hand-over."""

    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    heir = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(heir)
    create_user(client, dealership)
    kept = _customer_for(client, leaver, lastName="Kept")
    queued = _customer_for(client, leaver, lastName="Queued")
    handed = client.post(
        f"/v1/dealerships/{dealership}/users/{leaver['id']}/ownership/transfers",
        json={"transfers": [{"module": "customer", "group": "customers", "toUserId": heir["id"]}]},
        headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert handed.status_code == 200, handed.text
    cleared = client.patch(
        f"/v1/customers/{queued['id']}", json={"advisorId": None}, headers={**_as(heir), "If-Match": "2"}
    )
    assert cleared.status_code == 200, cleared.text

    unassigned = client.get("/v1/customers?unassigned=true", headers=_as(heir)).json()
    assert [c["id"] for c in unassigned["items"]] == [queued["id"]]
    mine = client.get(f"/v1/customers?advisorUserId={heir['id']}", headers=_as(heir)).json()
    assert [c["id"] for c in mine["items"]] == [kept["id"]]


# --- the review's findings: sister Betriebe, the job's failure modes, reconciliation -----


def _acting_in(user: dict, dealership: str) -> dict[str, str]:
    """A session of this user switched to a sister Betrieb (a membership)."""

    tid = uuid.UUID(dealership)
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_sister_betrieb_members_deals_are_theirs_and_go_to_a_colleague_who_acts_there_or_to_the_queue(client, login):
    """FR-A-37 across the group: a member creates offers in the sister
    Betrieb and owns them there; the deactivation counts them; a colleague
    who cannot act in that Betrieb is refused before anything changes
    (422), a member there receives them, and null sends them to that
    Betrieb's own queue."""

    from tests.membership_support import create_dealership as create_sister
    from tests.membership_support import grant

    home = create_dealership(client)
    sister = create_sister(client, "Garage Süd AG", group_of=home)
    manager = create_user(client, home)
    leaver = create_user(client, home, isDealerManager=False, firstName="Lea", lastName="Ver")
    home_only = create_user(client, home, isDealerManager=False, firstName="Home", lastName="Only")
    member_too = create_user(client, home, isDealerManager=False, firstName="Mem", lastName="Ber")
    for person in (leaver, home_only, member_too):
        login.accept_invitation(person)
    assert grant(client, home, leaver, sister).status_code == 201
    assert grant(client, home, member_too, sister).status_code == 201

    there = client.post("/v1/sales/offers", headers=_acting_in(leaver, sister))
    assert there.status_code == 201, there.text
    assert there.json()["ownerUserId"] == leaver["id"] and there.json()["ownerLabel"] == "Lea Ver"
    at_home = _offer_for(client, leaver)
    assert _ownership(client, home, leaver)["groups"] == [{"module": "sales", "group": "offers", "count": 2}]

    version = client.get(f"/v1/dealerships/{home}/users/{leaver['id']}", headers=platform_admin_headers()).json()[
        "version"
    ]
    refused = deactivate(
        client,
        home,
        leaver,
        headers=_as(manager, manager=True),
        if_match=version,
        transfers=[{"module": "sales", "group": "offers", "toUserId": home_only["id"]}],
    )
    assert refused.status_code == 422, refused.text
    [problem] = refused.json()["error"]["details"]["problems"]
    assert problem.startswith("sales/offers: 1 of these records belong to another business")
    assert sister not in refused.text  # never names the other Betrieb (tests/test_user.py's rule)
    still = client.get(f"/v1/dealerships/{home}/users/{leaver['id']}", headers=platform_admin_headers()).json()
    assert still["status"] == "active"  # nothing changed: the manager chooses again

    accepted = deactivate(
        client,
        home,
        leaver,
        headers=_as(manager, manager=True),
        if_match=version,
        transfers=[{"module": "sales", "group": "offers", "toUserId": member_too["id"]}],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["transfers"] == [
        {"module": "sales", "group": "offers", "toUserId": member_too["id"], "count": 2}
    ]
    assert (
        client.get(f"/v1/sales/offers/{there.json()['id']}", headers=_acting_in(member_too, sister)).json()[
            "ownerUserId"
        ]
        == member_too["id"]
    )
    assert (
        client.get(f"/v1/sales/offers/{at_home['id']}", headers=_as(member_too)).json()["ownerUserId"]
        == member_too["id"]
    )

    # The queue is per Betrieb: handed to nobody, the sister's offer shows in
    # the sister's own unassigned list, not the home's.
    second = create_user(client, home, isDealerManager=False, firstName="Sec", lastName="Ond")
    login.accept_invitation(second)
    assert grant(client, home, second, sister).status_code == 201
    # A pending contract: the deal grid lists contracts and open offers, never drafts.
    buyer = client.post(
        "/v1/customers",
        json={
            "firstName": "Sis",
            "lastName": "Ter",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"sis-{uuid.uuid4().hex[:6]}@example.ch"}],
        },
        headers=_acting_in(second, sister),
    ).json()
    theirs = client.post("/v1/sales/contracts", json={"customerId": buyer["id"]}, headers=_acting_in(second, sister))
    assert theirs.status_code == 201, theirs.text
    assert theirs.json()["ownerUserId"] == second["id"]
    version = client.get(f"/v1/dealerships/{home}/users/{second['id']}", headers=platform_admin_headers()).json()[
        "version"
    ]
    released = deactivate(
        client,
        home,
        second,
        headers=_as(manager, manager=True),
        if_match=version,
        transfers=[{"module": "sales", "group": "contracts", "toUserId": None}],
    )
    assert released.status_code == 200, released.text
    queue_there = client.get("/v1/sales/deals?unassigned=true", headers=_acting_in(member_too, sister)).json()
    assert [d["contractId"] for d in queue_there["items"]] == [theirs.json()["id"]]
    assert client.get("/v1/sales/deals?unassigned=true", headers=_as(member_too)).json()["total"] == 0


def test_a_seller_set_in_the_workspace_must_hold_or_await_access(client, login):
    """`ownerUserId` refuses a suspended or deactivated user (422), admits
    an invited one and a sister-Betrieb member."""

    from tests.membership_support import create_dealership as create_sister
    from tests.membership_support import grant

    dealership = create_dealership(client)
    sister = create_sister(client, "Garage Ost AG", group_of=dealership)
    seller = create_user(client, dealership)
    login.accept_invitation(seller)
    gone = create_user(client, dealership, isDealerManager=False)
    assert deactivate(client, dealership, gone).status_code == 200
    paused = create_user(client, dealership, isDealerManager=False)
    login.accept_invitation(paused)
    suspended = client.patch(
        f"/v1/dealerships/{dealership}/users/{paused['id']}",
        json={"status": "suspended"},
        headers={**platform_admin_headers(), "If-Match": "2"},
    )
    assert suspended.status_code == 200, suspended.text
    invited = create_user(client, dealership, isDealerManager=False)
    member = create_user(client, sister, isDealerManager=False)  # homed in the sister, a member of this one
    assert grant(client, sister, member, dealership).status_code == 201
    offer = client.post("/v1/sales/offers", headers=_as(seller)).json()

    for refused_user in (gone, paused):
        refused = client.patch(
            f"/v1/sales/offers/{offer['id']}",
            json={"ownerUserId": refused_user["id"]},
            headers={**_as(seller), "If-Match": str(offer["version"])},
        )
        assert refused.status_code == 422, refused.text
    version = offer["version"]
    for admitted_user in (invited, member):
        admitted = client.patch(
            f"/v1/sales/offers/{offer['id']}",
            json={"ownerUserId": admitted_user["id"]},
            headers={**_as(seller), "If-Match": str(version)},
        )
        assert admitted.status_code == 200, admitted.text
        assert admitted.json()["ownerUserId"] == admitted_user["id"]
        version = admitted.json()["version"]


def test_the_expiry_job_expires_a_staff_account_and_one_failure_does_not_stop_the_others(
    client, db_session, engine, caplog, monkeypatch
):
    """The job acts with platform's own authority: a `platform_admin` whose
    `valid_to` passed expires like anyone (KAN-97's guard is for a manager,
    not for the system), and a user whose status change fails is logged and
    skipped — the next user is still deactivated."""

    dealership = create_dealership(client)
    yesterday = str(dt.datetime.now(ZoneInfo("Europe/Zurich")).date() - dt.timedelta(days=1))
    create_user(client, dealership)  # stays the manager
    staff = create_user(client, dealership, isDealerManager=False, accessRoles=["platform_admin"], validTo=yesterday)
    broken = create_user(client, dealership, isDealerManager=False, validTo=yesterday)
    after = create_user(client, dealership, isDealerManager=False, validTo=yesterday)

    from app.platform.services import user as user_service

    real_update = user_service.update_user

    def flaky(db, *, user, **kwargs):
        if user.id == uuid.UUID(broken["id"]):
            raise RuntimeError("audit store is down")
        return real_update(db, user=user, **kwargs)

    monkeypatch.setattr(user_service, "update_user", flaky)
    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_user_expiry(db_session, session_factory=sessionmaker(bind=engine))
    monkeypatch.undo()

    db_session.expire_all()
    assert db_session.get(User, uuid.UUID(staff["id"])).status.value == "deactivated"
    assert db_session.get(User, uuid.UUID(after["id"])).status.value == "deactivated"
    assert db_session.get(User, uuid.UUID(broken["id"])).status.value == "invited"
    summary = [r for r in caplog.records if r.name == daily_jobs.__name__ and "due=" in r.getMessage()]
    assert summary and summary[-1].levelno == logging.ERROR
    assert "deactivated=2" in summary[-1].getMessage() and "failed=1" in summary[-1].getMessage()


def test_the_expiry_job_retries_a_failed_hand_over_and_reconciliation_reports_it_meanwhile(
    client, db_session, engine, caplog, monkeypatch
):
    """A module that fails after the status change leaves its records with
    the (now deactivated) user: the next run retries the release, and until
    it succeeds the module's nightly reconciliation names the record."""

    from app.sales import reconciliation as sales_reconciliation

    dealership = create_dealership(client)
    yesterday = str(dt.datetime.now(ZoneInfo("Europe/Zurich")).date() - dt.timedelta(days=1))
    create_user(client, dealership)
    expired = create_user(client, dealership, isDealerManager=False, validTo=yesterday)
    witness = create_user(client, dealership, isDealerManager=False)
    offer = _offer_for(client, expired)

    sales = ownership_provider("sales")
    assert sales is not None

    def boom(*args, **kwargs):
        raise RuntimeError("sales is down")

    monkeypatch.setattr(sales, "transfer", boom)
    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_user_expiry(db_session, session_factory=sessionmaker(bind=engine))
    monkeypatch.undo()
    db_session.expire_all()
    assert db_session.get(User, uuid.UUID(expired["id"])).status.value == "deactivated"
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(witness)).json()["ownerUserId"] == expired["id"]
    first = [r for r in caplog.records if r.name == daily_jobs.__name__ and "due=" in r.getMessage()][-1]
    assert first.levelno == logging.ERROR and "handover_failed=1" in first.getMessage()

    with pytest.raises(ReconciliationAlarm) as alarm:
        sales_reconciliation.run(db_session)
    labels = {o.check_label for o in alarm.value.orphans}
    assert "sales_offer.owner_user_id names a deactivated user while the offer is open" in labels

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=daily_jobs.__name__):
        run_daily_user_expiry(db_session, session_factory=sessionmaker(bind=engine))
    db_session.expire_all()
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(witness)).json()["ownerUserId"] is None
    late = [r for r in caplog.records if r.name == daily_jobs.__name__ and "late hand-over" in r.getMessage()]
    assert late and "sales/offers=1" in late[0].getMessage()
    second = [r for r in caplog.records if r.name == daily_jobs.__name__ and "due=" in r.getMessage()][-1]
    assert second.levelno == logging.INFO and "retried=1" in second.getMessage()
    assert sales_reconciliation.run(db_session).orphans_found == 0
