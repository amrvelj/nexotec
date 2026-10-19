"""KAN-200 (Anto, 2026-10-19) — the responsible seller on offers and
contracts: the creator by default when they are a user of the dealership,
re-assignable from the workspace, copied to the contract and the deal grid,
filterable there, and cleared ("unassigned") for the queue FR-A-37 names."""

import uuid

from app.core.auth import AccessRole, create_access_token
from tests.identity_support import create_dealership, create_user, platform_admin_headers


def _as(user: dict) -> dict[str, str]:
    tid = uuid.UUID(user["dealershipId"])
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _buyer(client, user: dict) -> dict:
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Ursula",
            "lastName": "Vogt",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": f"u-{uuid.uuid4().hex[:6]}@example.ch"}],
        },
        headers=_as(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_creator_is_the_responsible_seller_and_a_copy_is_the_copiers(client):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, firstName="Sara", lastName="Seller")
    copier = create_user(client, dealership, isDealerManager=False, firstName="Carl", lastName="Copier")

    offer = client.post("/v1/sales/offers", headers=_as(seller)).json()
    assert offer["ownerUserId"] == seller["id"] and offer["ownerLabel"] == "Sara Seller"

    copied = client.post(f"/v1/sales/offers/{offer['id']}/copy", headers=_as(copier))
    assert copied.status_code == 201, copied.text
    assert copied.json()["ownerUserId"] == copier["id"] and copied.json()["ownerLabel"] == "Carl Copier"


def test_platform_staff_creating_an_offer_in_a_betrieb_they_are_not_homed_in_own_nothing(client):
    dealership = create_dealership(client)
    headers = platform_admin_headers(tenant_id=uuid.UUID(dealership))
    offer = client.post("/v1/sales/offers", headers=headers)
    assert offer.status_code == 201, offer.text
    assert offer.json()["ownerUserId"] is None and offer.json()["ownerLabel"] is None


def test_the_seller_can_be_handed_on_cleared_and_must_be_a_user_of_the_dealership(client):
    dealership = create_dealership(client)
    seller = create_user(client, dealership)
    colleague = create_user(client, dealership, isDealerManager=False, firstName="Kim", lastName="Koller")
    stranger = create_user(client, create_dealership(client))
    offer = client.post("/v1/sales/offers", headers=_as(seller)).json()

    handed = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": colleague["id"]},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["ownerUserId"] == colleague["id"] and handed.json()["ownerLabel"] == "Kim Koller"

    refused = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": stranger["id"]},
        headers={**_as(seller), "If-Match": str(handed.json()["version"])},
    )
    assert refused.status_code == 422, refused.text

    cleared = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": None},
        headers={**_as(seller), "If-Match": str(handed.json()["version"])},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ownerUserId"] is None and cleared.json()["ownerLabel"] is None


def test_a_contract_takes_the_offers_seller_and_a_direct_one_its_creator_and_the_grid_filters(client):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, firstName="Sara", lastName="Seller")
    other = create_user(client, dealership, isDealerManager=False, firstName="Otto", lastName="Other")
    buyer = _buyer(client, seller)

    offer = client.post("/v1/sales/offers", headers=_as(seller)).json()
    from_offer = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(other))
    assert from_offer.status_code == 201, from_offer.text
    assert from_offer.json()["ownerUserId"] == seller["id"] and from_offer.json()["ownerLabel"] == "Sara Seller"

    direct = client.post("/v1/sales/contracts", json={"customerId": buyer["id"]}, headers=_as(other))
    assert direct.status_code == 201, direct.text
    assert direct.json()["ownerUserId"] == other["id"] and direct.json()["ownerLabel"] == "Otto Other"

    handed = client.patch(
        f"/v1/sales/contracts/{direct.json()['id']}",
        json={"ownerUserId": None},
        headers={**_as(other), "If-Match": str(direct.json()["version"])},
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["ownerUserId"] is None

    deals = client.get("/v1/sales/deals", headers=_as(seller)).json()["items"]
    owners = {d["contractId"]: (d["ownerUserId"], d["ownerLabel"]) for d in deals}
    assert owners[from_offer.json()["id"]] == (seller["id"], "Sara Seller")
    assert owners[direct.json()["id"]] == (None, None)
    mine = client.get(f"/v1/sales/deals?ownerUserId={seller['id']}", headers=_as(seller)).json()
    assert [d["contractId"] for d in mine["items"]] == [from_offer.json()["id"]]
    unassigned = client.get("/v1/sales/deals?unassigned=true", headers=_as(seller)).json()
    assert [d["contractId"] for d in unassigned["items"]] == [direct.json()["id"]]
