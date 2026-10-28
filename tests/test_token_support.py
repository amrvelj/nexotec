"""KAN-424 — test tokens carry their Betrieb's real dealer group, as a minted
session does (`_mint_session`), never a uuid5 of the tenant no Betrieb has."""

import uuid

from sqlalchemy import select

from app.core.auth import AccessRole, decode_token_claims
from app.customer.models.customer import Customer
from tests.identity_support import create_dealership, platform_admin_headers
from tests.membership_support import bearer, token
from tests.token_support import dealer_group_of, synthetic_group_of


def _dealer_group_id(client, dealership: str) -> uuid.UUID:
    body = client.get(f"/v1/dealerships/{dealership}", headers=platform_admin_headers()).json()
    return uuid.UUID(body["dealerGroupId"])


def test_the_shared_helpers_mint_the_betriebs_real_dealer_group(client):
    dealership = create_dealership(client)
    real = _dealer_group_id(client, dealership)

    assert dealer_group_of(dealership) == real
    assert dealer_group_of(uuid.UUID(dealership)) == real
    assert real != synthetic_group_of(dealership)

    manager = token(AccessRole.SALES, tenant_id=dealership, is_dealer_manager=True)
    assert uuid.UUID(decode_token_claims(manager)["group_id"]) == real
    staff = platform_admin_headers(tenant_id=uuid.UUID(dealership))["Authorization"].removeprefix("Bearer ")
    assert uuid.UUID(decode_token_claims(staff)["group_id"]) == real


def test_a_customer_created_with_a_helper_token_lands_in_the_betriebs_group(client, db_session):
    """What KAN-423's hand-over compares: the customer's group_id is the one
    a real session in that Betrieb would have written."""

    dealership = create_dealership(client)
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Ada",
            "lastName": "Byron",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": "ada-kan424@example.ch"}],
        },
        headers=bearer(token(AccessRole.SALES, tenant_id=dealership)),
    )
    assert response.status_code == 201, response.text

    stored = db_session.execute(
        select(Customer.group_id).where(Customer.id == uuid.UUID(response.json()["id"]))
    ).scalar_one()
    assert stored == _dealer_group_id(client, dealership)


def test_a_tenant_without_a_betrieb_keeps_the_synthetic_group_until_it_exists(client):
    """A token minted before its Betrieb exists has no real group; the miss is
    not cached, so the next token for that id, once it exists, carries it."""

    unknown = uuid.uuid4()
    assert dealer_group_of(unknown) == synthetic_group_of(unknown)

    dealership = create_dealership(client)
    assert dealer_group_of(dealership) == _dealer_group_id(client, dealership)
