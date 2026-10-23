"""KAN-423 (Anto, 2026-10-23, option A) — a deactivated member's customers
are never handed to a colleague who works in no Betrieb of the customers'
dealer group. Since KAN-410 a sister-Betrieb member advises the customers
they create there, and a membership may span dealer groups; the customer
hand-over now refuses such a colleague as a whole — Sales' rule for deals,
with Sales' reason `colleague_cannot_act_there` and a count, never the other
group — and the manager sends the group to the unassigned queue instead.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.ownership import ownership_provider
from tests.identity_support import PasswordLogin, create_dealership, create_user, deactivate, platform_admin_headers
from tests.membership_support import create_dealership as create_sister
from tests.membership_support import grant


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _acting_in(client, user: dict, dealership: str) -> dict[str, str]:
    """A session of `user` in `dealership`, carrying the Betrieb's REAL dealer
    group as a minted session does — the customer's `group_id` is what the
    refusal compares, so a made-up group would refuse every hand-over."""

    group = client.get(f"/v1/dealerships/{dealership}", headers=platform_admin_headers()).json()["dealerGroupId"]
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=uuid.UUID(dealership),
        group_id=uuid.UUID(group),
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _customer_created_by(client, user: dict, dealership: str) -> dict:
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada",
            "lastName": "Byron",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"ada-{uuid.uuid4().hex[:6]}@example.ch"}],
        },
        headers=_acting_in(client, user, dealership),
    )
    assert response.status_code == 201, response.text
    assert response.json()["advisorId"] == user["id"]
    return response.json()


def _version(client, dealership: str, user: dict) -> int:
    return client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()[
        "version"
    ]


def _member_of(client, login: PasswordLogin, home: str, other: str) -> tuple[dict, dict]:
    """A leaver homed in `home` and granted `other`; an active colleague homed in `home` only."""

    leaver = create_user(client, home, isDealerManager=False, firstName="Lea", lastName="Ver")
    colleague = create_user(client, home, isDealerManager=False, firstName="Kai", lastName="Home")
    login.accept_invitation(colleague)
    assert grant(client, home, leaver, other).status_code == 201
    return leaver, colleague


def test_a_hand_over_of_customers_from_another_dealer_group_is_refused_and_the_queue_takes_them(client, login):
    home = create_dealership(client)
    create_user(client, home)  # the manager who stays
    other_group = create_dealership(client)  # a Betrieb of another dealer group
    leaver, colleague = _member_of(client, login, home, other_group)
    customer = _customer_created_by(client, leaver, other_group)
    version = _version(client, home, leaver)

    refused = deactivate(
        client,
        home,
        leaver,
        if_match=version,
        transfers=[{"module": "customer", "group": "customers", "toUserId": colleague["id"]}],
    )

    assert refused.status_code == 422, refused.text
    [problem] = refused.json()["error"]["details"]["problems"]
    assert (problem["module"], problem["group"], problem["reason"], problem["count"]) == (
        "customer",
        "customers",
        "colleague_cannot_act_there",
        1,
    )
    other_dealer_group = client.get(f"/v1/dealerships/{other_group}", headers=platform_admin_headers()).json()[
        "dealerGroupId"
    ]
    assert other_group not in refused.text and other_dealer_group not in refused.text  # never names either
    assert _version(client, home, leaver) == version  # nothing written: the step refused before access was cut
    still = client.get(f"/v1/customers/{customer['id']}", headers=_acting_in(client, leaver, other_group))
    assert still.json()["advisorId"] == leaver["id"]

    released = deactivate(
        client,
        home,
        leaver,
        if_match=version,
        transfers=[{"module": "customer", "group": "customers", "toUserId": None}],
    )

    assert released.status_code == 200, released.text
    reader = create_user(client, other_group, firstName="Rita", lastName="Reader")
    queued = client.get("/v1/customers?unassigned=true", headers=_acting_in(client, reader, other_group))
    assert queued.status_code == 200, queued.text
    assert customer["id"] in {row["id"] for row in queued.json()["items"]}  # the customers' own group's queue
    after = client.get(f"/v1/customers/{customer['id']}", headers=_acting_in(client, reader, other_group))
    assert after.json()["advisorId"] is None


def test_a_hand_over_inside_the_customers_dealer_group_still_goes_to_the_colleague(client, login):
    home = create_dealership(client)
    create_user(client, home)
    sister = create_sister(client, "Garage Süd AG", group_of=home)
    leaver, colleague = _member_of(client, login, home, sister)
    customer = _customer_created_by(client, leaver, sister)

    handed = deactivate(
        client,
        home,
        leaver,
        if_match=_version(client, home, leaver),
        transfers=[{"module": "customer", "group": "customers", "toUserId": colleague["id"]}],
    )

    assert handed.status_code == 200, handed.text
    after = client.get(f"/v1/customers/{customer['id']}", headers=_acting_in(client, colleague, home))
    assert after.status_code == 200, after.text
    assert after.json()["advisorId"] == colleague["id"]


def test_a_colleague_who_is_a_member_in_the_other_group_may_take_them(client, login):
    home = create_dealership(client)
    create_user(client, home)
    other_group = create_dealership(client)
    leaver, colleague = _member_of(client, login, home, other_group)
    assert grant(client, home, colleague, other_group).status_code == 201
    customer = _customer_created_by(client, leaver, other_group)

    handed = deactivate(
        client,
        home,
        leaver,
        if_match=_version(client, home, leaver),
        transfers=[{"module": "customer", "group": "customers", "toUserId": colleague["id"]}],
    )

    assert handed.status_code == 200, handed.text
    after = client.get(f"/v1/customers/{customer['id']}", headers=_acting_in(client, colleague, other_group))
    assert after.json()["advisorId"] == colleague["id"]


def test_the_hand_over_judges_the_colleague_again_under_the_rows_locks(client, login, monkeypatch):
    """`refuses` reads the customers unlocked, before access is cut; the
    transfer locks them and judges again before it writes (a membership could
    end, or a customer be created, in between). Standing in for that race,
    `refuses` lets everything through: the transfer still refuses — answered
    as the group's `failed` entry with the same reason, the customer left
    with the leaver for the retry."""

    home = create_dealership(client)
    create_user(client, home)
    other_group = create_dealership(client)
    leaver, colleague = _member_of(client, login, home, other_group)
    customer = _customer_created_by(client, leaver, other_group)
    customers = ownership_provider("customer")
    assert customers is not None
    monkeypatch.setattr(customers, "refuses", lambda *args, **kwargs: None)

    response = deactivate(
        client,
        home,
        leaver,
        if_match=_version(client, home, leaver),
        transfers=[{"module": "customer", "group": "customers", "toUserId": colleague["id"]}],
    )

    assert response.status_code == 200, response.text
    [failed] = response.json()["failed"]
    assert (failed["module"], failed["group"], failed["reason"]) == ("customer", "customers", "colleague_cannot_act_there")
    still = client.get(f"/v1/customers/{customer['id']}", headers=_acting_in(client, leaver, other_group))
    assert still.json()["advisorId"] == leaver["id"]
    owned = client.get(f"/v1/dealerships/{home}/users/{leaver['id']}/ownership", headers=platform_admin_headers())
    assert {"module": "customer", "group": "customers", "count": 1} in owned.json()["groups"]
