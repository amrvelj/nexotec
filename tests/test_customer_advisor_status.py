"""KAN-410 — a customer's advisor must be someone who may own an open record
in the acting Betrieb, platform's one rule for Sales' responsible seller too
(`platform.public.get_active_user_acting_in`): homed there or an active
member of it, and active or invited. Create and PATCH refuse anyone else
(422 naming `advisorId`); a PATCH that leaves an already-deactivated advisor
unchanged still saves. Anto, 2026-10-23 (option B): a sister-Betrieb member
who works here may advise — picked, or by default on the customers they
create here (D-24) — and the advisor picker lists them.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.platform.models.user import User, UserStatus
from tests.membership_support import create_dealership as create_sister
from tests.membership_support import grant, if_match
from tests.test_customer_fr17_fields import _bearer, _create_dealer, _create_user, _token


def _set_status(db_session, user: dict, status: UserStatus) -> None:
    # Set on the row: the paths that leave a deactivated or suspended user
    # behind (a deactivation, a suspension PATCH) are not what this tests.
    db_session.get(User, uuid.UUID(user["id"])).status = status
    db_session.commit()


def _setup(client) -> tuple[str, dict, str]:
    dealer_id = _create_dealer(client)
    acting = _create_user(client, dealer_id, firstName="Acting", lastName="User")
    return dealer_id, acting, _token(tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(acting["id"]))


def _create_body(advisor_id: str | None = None) -> dict:
    body = {
        "firstName": "Ada",
        "lastName": "Byron",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    if advisor_id is not None:
        body["advisorId"] = advisor_id
    return body


@pytest.mark.parametrize("status", [UserStatus.DEACTIVATED, UserStatus.SUSPENDED])
def test_create_refuses_an_advisor_who_is_not_active_or_invited(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    advisor = _create_user(client, dealer_id, firstName="Left", lastName="Already")
    _set_status(db_session, advisor, status)

    resp = client.post("/v1/customers", json=_create_body(advisor["id"]), headers=_bearer(token))

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["details"]["advisorId"] == advisor["id"]


@pytest.mark.parametrize("status", [UserStatus.DEACTIVATED, UserStatus.SUSPENDED])
def test_patch_refuses_an_advisor_who_is_not_active_or_invited(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    created = client.post("/v1/customers", json=_create_body(), headers=_bearer(token)).json()
    advisor = _create_user(client, dealer_id, firstName="Left", lastName="Already")
    _set_status(db_session, advisor, status)

    resp = client.patch(
        f"/v1/customers/{created['id']}",
        json={"advisorId": advisor["id"]},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["details"]["advisorId"] == advisor["id"]


@pytest.mark.parametrize("status", [UserStatus.ACTIVE, UserStatus.INVITED])
def test_an_active_or_invited_advisor_is_accepted_on_create_and_patch(client, db_session, status):
    dealer_id, _acting, token = _setup(client)
    first = _create_user(client, dealer_id, firstName="First", lastName="Advisor")
    second = _create_user(client, dealer_id, firstName="Second", lastName="Advisor")
    _set_status(db_session, first, status)
    _set_status(db_session, second, status)

    created = client.post("/v1/customers", json=_create_body(first["id"]), headers=_bearer(token))
    assert created.status_code == 201, created.text
    assert created.json()["advisorLabel"] == "First Advisor"

    patched = client.patch(
        f"/v1/customers/{created.json()['id']}",
        json={"advisorId": second["id"]},
        headers={**_bearer(token), "If-Match": str(created.json()["version"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["advisorId"] == second["id"]
    assert patched.json()["advisorLabel"] == "Second Advisor"


def test_an_unchanged_deactivated_advisor_does_not_block_another_fields_patch(client, db_session):
    dealer_id, _acting, token = _setup(client)
    advisor = _create_user(client, dealer_id, firstName="Mia", lastName="Frei")
    created = client.post("/v1/customers", json=_create_body(advisor["id"]), headers=_bearer(token)).json()
    _set_status(db_session, advisor, UserStatus.DEACTIVATED)

    # Once without advisorId, once naming the same advisor: neither changes it.
    first = client.patch(
        f"/v1/customers/{created['id']}",
        json={"nextFollowUp": "2026-12-01"},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/v1/customers/{created['id']}",
        json={"advisorId": advisor["id"], "nextFollowUp": "2026-12-02"},
        headers={**_bearer(token), "If-Match": str(first.json()["version"])},
    )
    assert second.status_code == 200, second.text
    assert second.json()["advisorId"] == advisor["id"]
    assert second.json()["advisorLabel"] == "Mia Frei"


# --- a sister-Betrieb member (Anto, 2026-10-23, option B) ------------------------------------


def _sister_with_member(client) -> tuple[str, str, dict]:
    """A home Betrieb, its sister, and a home user granted a membership there."""

    home = _create_dealer(client)
    sister = create_sister(client, "Garage Süd AG", group_of=home)
    member = _create_user(client, home, firstName="Mia", lastName="Member")
    assert grant(client, home, member, sister).status_code == 201
    return home, sister, member


def _acting_in(user_id: str, dealership: str) -> dict[str, str]:
    tid = uuid.UUID(dealership)
    token = create_access_token(
        user_id=uuid.UUID(user_id),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_sister_betrieb_member_may_be_picked_as_advisor_there(client):
    _home, sister, member = _sister_with_member(client)
    colleague = _create_user(client, sister, firstName="Sven", lastName="Sister")
    headers = _acting_in(colleague["id"], sister)

    created = client.post("/v1/customers", json=_create_body(member["id"]), headers=headers)

    assert created.status_code == 201, created.text
    assert (created.json()["advisorId"], created.json()["advisorLabel"]) == (member["id"], "Mia Member")


def test_a_sister_betrieb_member_creating_a_customer_there_becomes_its_advisor(client):
    """D-24's default, the same way a member's offer there is theirs (KAN-200)."""

    _home, sister, member = _sister_with_member(client)

    created = client.post("/v1/customers", json=_create_body(), headers=_acting_in(member["id"], sister))

    assert created.status_code == 201, created.text
    assert (created.json()["advisorId"], created.json()["advisorLabel"]) == (member["id"], "Mia Member")


def test_a_patch_there_may_hand_a_customer_to_a_member_and_not_to_an_ended_one(client):
    home, sister, member = _sister_with_member(client)
    colleague = _create_user(client, sister, firstName="Sven", lastName="Sister")
    headers = _acting_in(colleague["id"], sister)
    customer = client.post("/v1/customers", json=_create_body(), headers=headers).json()

    patched = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"advisorId": member["id"]},
        headers={**headers, "If-Match": str(customer["version"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["advisorLabel"] == "Mia Member"

    later = _create_user(client, home, firstName="Lena", lastName="Later")
    assert grant(client, home, later, sister).status_code == 201
    ended = client.delete(
        f"/v1/dealerships/{home}/users/{later['id']}/memberships/{sister}", headers=if_match(client, home, later)
    )
    assert ended.status_code in (200, 204), ended.text
    refused = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"advisorId": later["id"]},
        headers={**headers, "If-Match": str(patched.json()["version"])},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["advisorId"] == later["id"]


def test_a_member_whose_membership_ended_or_a_user_of_another_betrieb_is_refused(client):
    home, sister, member = _sister_with_member(client)
    stranger_betrieb = _create_dealer(client)
    stranger = _create_user(client, stranger_betrieb, firstName="Otto", lastName="Other")
    colleague = _create_user(client, sister, firstName="Sven", lastName="Sister")
    headers = _acting_in(colleague["id"], sister)

    other = client.post("/v1/customers", json=_create_body(stranger["id"]), headers=headers)
    assert other.status_code == 422, other.text
    assert other.json()["error"]["details"]["advisorId"] == stranger["id"]

    ended = client.delete(
        f"/v1/dealerships/{home}/users/{member['id']}/memberships/{sister}",
        headers=if_match(client, home, member),
    )
    assert ended.status_code in (200, 204), ended.text
    refused = client.post("/v1/customers", json=_create_body(member["id"]), headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["advisorId"] == member["id"]


def test_the_advisor_picker_lists_the_betriebs_active_members_and_no_one_else(client, db_session):
    home, sister, member = _sister_with_member(client)
    suspended_member = _create_user(client, home, firstName="Sara", lastName="Suspended")
    assert grant(client, home, suspended_member, sister).status_code == 201
    _set_status(db_session, suspended_member, UserStatus.SUSPENDED)
    ended_member = _create_user(client, home, firstName="Elsa", lastName="Ended")
    assert grant(client, home, ended_member, sister).status_code == 201
    ended = client.delete(
        f"/v1/dealerships/{home}/users/{ended_member['id']}/memberships/{sister}",
        headers=if_match(client, home, ended_member),
    )
    assert ended.status_code in (200, 204), ended.text
    own = _create_user(client, sister, firstName="Sven", lastName="Sister")
    home_only = _create_user(client, home, firstName="Hugo", lastName="Home")

    response = client.get("/v1/customers/advisor-options", headers=_acting_in(own["id"], sister))

    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()["items"]}
    assert {member["id"], own["id"]} <= ids
    assert suspended_member["id"] not in ids
    assert ended_member["id"] not in ids
    assert home_only["id"] not in ids
