"""KAN-379, ADR-106: a dealership membership may cross dealer groups.

Anto ruled on 2026-10-23 (KAN-423) for option A, refusing a hand-over of
customers to a colleague who works in no Betrieb of their group, and did not
take option B, "memberships never cross groups" (nor both). So a person who
genuinely works for two dealer groups holds a membership in a Betrieb of the
other group; switching there mints that Betrieb's own group (ADR-014's
group-scoped reads follow the active Betrieb, never the home), and the
hand-over guard of KAN-423 is what keeps a leaver's records with someone who
can work them. This pins the grant and the switch, so a later change that
starts refusing either is a visible reversal of the ruling, not an accident.
"""

import pytest

from app.core.auth import decode_token_claims
from tests.identity_support import (
    PasswordLogin,
    cookie_headers,
    create_dealership,
    create_user,
    platform_admin_headers,
)


@pytest.fixture
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _group_of(client, dealership_id: str) -> str:
    response = client.get(f"/v1/dealerships/{dealership_id}", headers=platform_admin_headers())
    assert response.status_code == 200, response.text
    return response.json()["dealerGroupId"]


def test_a_membership_in_a_betrieb_of_another_dealer_group_is_granted_and_the_switch_takes_that_groups_scope(
    client, login
):
    home = create_dealership(client, legalName="Garage Nord AG")
    other = create_dealership(client, legalName="Autohaus Ost AG")
    assert _group_of(client, home) != _group_of(client, other), "the two Betriebe must sit in different dealer groups"
    person = create_user(client, home, accessRoles=["sales"], isDealerManager=False)

    granted = client.post(
        f"/v1/dealerships/{home}/users/{person['id']}/memberships",
        json={"dealershipId": other, "isDealerManager": False},
        headers=platform_admin_headers(),
    )
    assert granted.status_code == 201, granted.text

    cookie = login.session_cookie(person)
    at_home = decode_token_claims(cookie)
    switched = client.post("/v1/auth/switch-dealership", json={"dealershipId": other}, headers=cookie_headers(cookie))

    assert switched.status_code == 200, switched.text
    assert switched.json()["activeDealership"]["id"] == other
    claims = decode_token_claims(switched.cookies["dms_session"])
    assert claims["tenant_id"] == other
    assert claims["group_id"] == _group_of(client, other)
    assert at_home["group_id"] == _group_of(client, home)
