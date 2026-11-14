"""KAN-214 (D-A-21, FR-A-39; Anto, 2026-11-13, Q1 A): the activation gate
and the legal hold, judged for a live session by the session guard.

- While the Betrieb is `pending_onboarding`, a session reaches Dealer
  Administration and My account; every other route answers 404.
- In an active Betrieb whose grace for a new ToS / DPA version has passed,
  a manager reaches only the acceptance (403 `legalAcceptanceRequired`);
  colleagues keep working.
"""

import datetime as dt

from app.main import app
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import legal_documents
from app.platform.session_guard import _ADMINISTRATION_MODULE_PREFIXES
from tests import setup_checklist_support as s
from tests.identity_support import PasswordLogin, cookie_headers
from tests.route_walk import api_routes


def _session(client, mailbox, user) -> dict[str, str]:
    return cookie_headers(PasswordLogin(client, mailbox).session_cookie(user))


def _set_status(db_session, dealership_id, status: DealershipStatus) -> None:
    dealership = db_session.get(Dealership, dealership_id)
    dealership.status = status
    db_session.commit()


def test_a_betrieb_being_set_up_reaches_administration_and_my_account_only(client, db_session, mailbox):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id, active=False)
    headers = _session(client, mailbox, boss)

    assert client.get("/v1/customers", headers=headers).status_code == 404
    assert client.get("/v1/vehicles", headers=headers).status_code == 404
    assert client.get("/v1/inventory/stock-items", headers=headers).status_code == 404
    assert client.get(f"/v1/dealerships/{dealership_id}/setup-checklist", headers=headers).status_code == 200
    assert client.get(f"/v1/dealerships/{dealership_id}/number-ranges", headers=headers).status_code == 200
    assert client.get("/v1/me", headers=headers).status_code == 200
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["activeDealership"]["status"] == "pending_onboarding"

    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    assert client.get("/v1/customers", headers=headers).status_code == 200
    assert client.get("/v1/auth/me", headers=headers).json()["activeDealership"]["status"] == "active"


def test_a_colleague_of_a_betrieb_being_set_up_is_gated_too(client, db_session, mailbox):
    dealership_id = s.pending_dealership(client, db_session)
    clerk = s.manager(client, db_session, dealership_id, active=False, isDealerManager=False)
    headers = _session(client, mailbox, clerk)
    assert client.get("/v1/customers", headers=headers).status_code == 404
    assert client.get("/v1/me", headers=headers).status_code == 200


def test_the_legal_hold_holds_the_manager_after_the_grace_and_no_one_else(client, db_session, mailbox):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id, active=False)
    clerk = s.manager(client, db_session, dealership_id, active=False, isDealerManager=False)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    # Effective long ago with 30 days' grace: the grace has passed.
    documents = s.publish_both(db_session, "1.0", effective_date=dt.date(2026, 1, 1))
    boss_headers = _session(client, mailbox, boss)
    clerk_headers = _session(client, mailbox, clerk)

    held = client.get("/v1/customers", headers=boss_headers)
    assert held.status_code == 403
    assert held.json()["error"]["details"] == {"legalAcceptanceRequired": True}
    assert client.get("/v1/customers", headers=clerk_headers).status_code == 200

    me = client.get("/v1/auth/me", headers=boss_headers).json()
    assert me["legalAcceptanceDue"]["held"] is True
    assert {d["documentType"] for d in me["legalAcceptanceDue"]["documents"]} == {"tos", "dpa"}
    assert client.get("/v1/auth/me", headers=clerk_headers).json()["legalAcceptanceDue"] is None

    assert client.get("/v1/legal-documents/current", headers=boss_headers).status_code == 200
    s.accept_both(client, dealership_id, documents, boss_headers)
    assert client.get("/v1/customers", headers=boss_headers).status_code == 200
    assert client.get("/v1/auth/me", headers=boss_headers).json()["legalAcceptanceDue"] is None


def test_within_the_grace_the_manager_is_prompted_not_held(client, db_session, mailbox):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id, active=False)
    _set_status(db_session, dealership_id, DealershipStatus.ACTIVE)
    s.publish_both(db_session, "1.0", effective_date=legal_documents.swiss_today())
    headers = _session(client, mailbox, boss)
    assert client.get("/v1/customers", headers=headers).status_code == 200
    due = client.get("/v1/auth/me", headers=headers).json()["legalAcceptanceDue"]
    assert due["held"] is False
    assert due["dueBy"] == (legal_documents.swiss_today() + dt.timedelta(days=30)).isoformat()


def test_only_administration_routes_pass_the_activation_gate():
    """The gate is judged by the route's endpoint module: every platform route
    and the Betrieb's integration connections pass; no module route does."""

    allowed, gated = set(), set()
    for path, route in api_routes(app.routes):
        if not path.startswith("/v1/"):
            continue
        module = route.endpoint.__module__
        (allowed if module.startswith(_ADMINISTRATION_MODULE_PREFIXES) else gated).add(module.split(".")[1])
    assert allowed == {"platform", "integration"}
    assert {"customer", "vehicle", "inventory", "sales", "valuation"} <= gated
    assert "platform" not in gated
