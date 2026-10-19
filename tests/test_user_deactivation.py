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
    response = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/ownership", headers=platform_admin_headers())
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

    response = deactivate(client, dealership, leaver, transfers=[{"module": "sales", "group": "offers", "toUserId": None}])
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["problems"] == ["customer/customers needs a decision: a colleague, or null to leave it unassigned"]
    assert {(g["module"], g["group"]) for g in details["openGroups"]} == {("customer", "customers"), ("sales", "offers")}
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
    assert any(r.after == {"advisor_id": heir["id"]} and r.reason == "advisor_deactivated" for r in customer_rows)


def test_a_failed_module_leaves_its_records_for_the_retry_and_the_user_deactivated(client, db_session, monkeypatch):
    """ADR-047: no shared transaction across the seam. The customer module's
    hand-over commits; sales fails; the user is deactivated (access cut
    first), the customer transfer is recorded, and the offers are still
    theirs for `POST …/ownership/transfers`."""

    dealership = create_dealership(client)
    leaver = create_user(client, dealership, isDealerManager=False)
    heir = create_user(client, dealership, isDealerManager=False)
    create_user(client, dealership)
    customer = _customer_for(client, leaver)
    _offer_for(client, leaver)

    sales = ownership_provider("sales")
    assert sales is not None

    def boom(*args, **kwargs):
        raise RuntimeError("sales is down")

    monkeypatch.setattr(sales, "transfer", boom)
    with pytest.raises(RuntimeError):
        deactivate(
            client,
            dealership,
            leaver,
            transfers=[
                {"module": "customer", "group": "customers", "toUserId": heir["id"]},
                {"module": "sales", "group": "offers", "toUserId": heir["id"]},
            ],
        )
    monkeypatch.undo()

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


def test_the_hand_over_endpoint_also_serves_a_user_who_stays(client):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, isDealerManager=False)
    heir = create_user(client, dealership, isDealerManager=False)
    offer = _offer_for(client, seller)
    response = client.post(
        f"/v1/dealerships/{dealership}/users/{seller['id']}/ownership/transfers",
        json={"transfers": [{"module": "sales", "group": "offers", "toUserId": heir["id"]}]},
        headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(heir)).json()["ownerUserId"] == heir["id"]
    assert client.get(f"/v1/dealerships/{dealership}/users/{seller['id']}", headers=platform_admin_headers()).json()["status"] == "invited"


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
    assert deactivate(client, dealership, manager, headers=_as(manager, manager=True)).status_code == 400  # last manager


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
    reissued = client.post(f"/v1/dealerships/{dealership}/users/{user['id']}/invitations", headers=platform_admin_headers())
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

    closed = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"validTo": str(today - dt.timedelta(days=1))},
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
