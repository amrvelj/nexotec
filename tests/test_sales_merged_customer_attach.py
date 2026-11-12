"""KAN-656: an offer or a contract never takes a merged customer as its buyer.

Before, Sales refused only a do-not-contact customer at attach, so a stale
screen (or an API call) could PATCH an offer or a contract with the
duplicate's id after `sales.customer_merged` had moved every deal to the
survivor (200), leaving the deal on a record that no longer exists as a
working record. The refusal is the allocation's (KAN-365): 409
`customer_merged`, naming the live customer the merge chain ends at.
"""

from app.core.auth import AccessRole
from tests.test_customer_merge_repoint import _merge
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_offer_contract_api import _bearer, _buyer, _token
from tests.test_sales_offer_refinements import _stock_item


def _setup(client, db_session):
    dealership = _dealership(db_session)
    token = _token(role=AccessRole.SALES, tenant_id=dealership.id)
    duplicate = _buyer(client, token, firstName="Ursi")
    survivor = _buyer(client, token)
    _merge(client, str(dealership.id), duplicate["id"], survivor["id"])
    return dealership, token, duplicate, survivor


def _assert_refused(response, survivor_id: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "customer_merged", "mergedIntoCustomerId": survivor_id}


def test_an_offer_patch_with_a_merged_customer_is_refused_and_names_the_survivor(client, db_session):
    _dealership_row, token, duplicate, survivor = _setup(client, db_session)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}", json={"customerId": duplicate["id"]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )

    _assert_refused(response, survivor["id"])
    after = client.get(f"/v1/sales/offers/{offer['id']}", headers=_bearer(token)).json()
    assert after["customerId"] is None
    assert after["version"] == offer["version"]


def test_an_offer_keeps_its_buyer_when_a_merged_customer_is_refused(client, db_session):
    _dealership_row, token, duplicate, survivor = _setup(client, db_session)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    attached = client.patch(
        f"/v1/sales/offers/{offer['id']}", json={"customerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )
    assert attached.status_code == 200, attached.text

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}", json={"customerId": duplicate["id"]},
        headers={**_bearer(token), "If-Match": str(attached.json()["version"])},
    )

    _assert_refused(response, survivor["id"])
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=_bearer(token)).json()["customerId"] == survivor["id"]


def test_a_pending_contract_patch_with_a_merged_customer_is_refused(client, db_session):
    dealership, token, duplicate, survivor = _setup(client, db_session)
    item = _stock_item(db_session, dealership.id)
    contract = client.post("/v1/sales/contracts", json={"stockItemId": str(item.id)}, headers=_bearer(token)).json()

    response = client.patch(
        f"/v1/sales/contracts/{contract['id']}", json={"customerId": duplicate["id"]},
        headers={**_bearer(token), "If-Match": str(contract["version"])},
    )

    _assert_refused(response, survivor["id"])
    assert client.get(f"/v1/sales/contracts/{contract['id']}", headers=_bearer(token)).json()["customerId"] is None


def test_creating_a_contract_for_a_merged_customer_is_refused_and_leaves_no_contract(client, db_session):
    _dealership_row, token, duplicate, survivor = _setup(client, db_session)

    response = client.post("/v1/sales/contracts", json={"customerId": duplicate["id"]}, headers=_bearer(token))

    _assert_refused(response, survivor["id"])
    listed = client.get("/v1/sales/contracts", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []


def test_the_refusal_names_the_end_of_a_merge_chain(client, db_session):
    dealership, token, first, middle = _setup(client, db_session)
    last = _buyer(client, token, firstName="Ulla")
    _merge(client, str(dealership.id), middle["id"], last["id"])
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}", json={"customerId": first["id"]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )

    _assert_refused(response, last["id"])

