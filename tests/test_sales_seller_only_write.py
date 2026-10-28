"""KAN-405 (Anto, 2026-10-28, option A) — the responsible seller of an offer
already sent to the customer (status «offen») may change: a PATCH carrying
`ownerUserId` alone. The same narrow write is open on a pending contract
priced before contracts kept their inputs, whose price is otherwise
untouchable (KAN-67, KAN-315). That is what lets «Übernehmen» pick up any
deal in the «Nicht zugewiesen» queue. Everything else about an open offer
stays refused; a PATCH that carries any other field is refused as before.
"""

import uuid

from sqlalchemy import select

from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from tests.identity_support import create_dealership, create_user
from tests.test_sales_contract_edit import _buyerless_contract_priced_without_inputs
from tests.test_sales_lifecycle_reservation import _dealership
from tests.test_sales_owner import _as


def _open_unassigned_offer(client, db_session, seller: dict) -> dict:
    """An offer the seller created and sent, then handed to nobody — the
    queue's row."""

    offer = client.post("/v1/sales/offers", headers=_as(seller)).json()
    priced = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={
            "vehicleSource": "manual", "vehicleLabel": "VW Golf 8 GTI", "manualVehicleCondition": "new",
            "manualBasePrice": "36000.00", "discountType": "amount", "discountValue": "500.00",
        },
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert priced.status_code == 200, priced.text
    assert priced.json()["grossPrice"] == "35500.00"
    cleared = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": None},
        headers={**_as(seller), "If-Match": str(priced.json()["version"])},
    )
    assert cleared.status_code == 200, cleared.text
    stored = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    stored.status = OfferStatus.OPEN
    upsert_deal_projection(db_session, offer=stored)
    db_session.commit()
    return client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()


def test_an_open_offer_takes_a_seller_alone_and_leaves_the_queue(client, db_session):
    dealership = create_dealership(client)
    seller = create_user(client, dealership)
    colleague = create_user(client, dealership, isDealerManager=False, firstName="Kim", lastName="Koller")
    offer = _open_unassigned_offer(client, db_session, seller)
    queue = client.get("/v1/sales/deals?unassigned=true", headers=_as(colleague)).json()["items"]
    assert [d["offerId"] for d in queue] == [offer["id"]]

    taken = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": colleague["id"]},
        headers={**_as(colleague), "If-Match": str(offer["version"])},
    )

    assert taken.status_code == 200, taken.text
    body = taken.json()
    assert body["ownerUserId"] == colleague["id"] and body["ownerLabel"] == "Kim Koller"
    assert body["status"] == "open"
    assert body["version"] == offer["version"] + 1
    unchanged = (
        "grossPrice", "listPrice", "discountAmount", "vehicleSnapshotFrozenAt", "customerId", "vehicleLabel",
        "offerNumber",
    )
    assert {k: body[k] for k in unchanged} == {k: offer[k] for k in unchanged}
    assert body["grossPrice"] == "35500.00" and body["vehicleSnapshotFrozenAt"] is not None
    assert client.get("/v1/sales/deals?unassigned=true", headers=_as(colleague)).json()["items"] == []
    deal = db_session.scalar(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(offer["id"])))
    db_session.refresh(deal)
    assert deal.owner_user_id == uuid.UUID(colleague["id"])


def test_an_open_offer_still_refuses_every_other_field_and_a_seller_sent_with_one(client, db_session):
    dealership = create_dealership(client)
    seller = create_user(client, dealership)
    offer = _open_unassigned_offer(client, db_session, seller)

    for body in (
        {"discountType": "amount", "discountValue": "100.00"},
        {"ownerUserId": seller["id"], "vehicleLabel": "Anderes Auto"},
        {},
    ):
        response = client.patch(
            f"/v1/sales/offers/{offer['id']}", json=body, headers={**_as(seller), "If-Match": str(offer["version"])}
        )
        assert response.status_code == 409, (body, response.text)

    db_session.expire_all()
    stored = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert stored.owner_user_id is None and stored.version == offer["version"]


def test_the_seller_alone_is_still_checked_against_the_dealership(client, db_session):
    dealership = create_dealership(client)
    seller = create_user(client, dealership)
    stranger = create_user(client, create_dealership(client))
    offer = _open_unassigned_offer(client, db_session, seller)

    refused = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": stranger["id"]},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )

    assert refused.status_code == 422, refused.text


def test_a_cancelled_offer_and_one_with_a_live_contract_take_no_seller(client, db_session):
    dealership = create_dealership(client)
    seller = create_user(client, dealership)

    cancelled = _open_unassigned_offer(client, db_session, seller)
    stored = db_session.get(SalesOffer, uuid.UUID(cancelled["id"]))
    stored.status = OfferStatus.CANCELLED
    db_session.commit()
    response = client.patch(
        f"/v1/sales/offers/{cancelled['id']}",
        json={"ownerUserId": seller["id"]},
        headers={**_as(seller), "If-Match": str(cancelled["version"])},
    )
    assert response.status_code == 409, response.text

    contracted = _open_unassigned_offer(client, db_session, seller)
    contract = client.post("/v1/sales/contracts", json={"offerId": contracted["id"]}, headers=_as(seller))
    assert contract.status_code == 201, contract.text
    contracted = client.get(f"/v1/sales/offers/{contracted['id']}", headers=_as(seller)).json()
    response = client.patch(
        f"/v1/sales/offers/{contracted['id']}",
        json={"ownerUserId": seller["id"]},
        headers={**_as(seller), "If-Match": str(contracted["version"])},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "offer_has_contract"


def test_a_contract_priced_without_inputs_takes_a_seller_alone(client, db_session):
    dealership = _dealership(db_session)
    seller = create_user(client, str(dealership.id), firstName="Sara", lastName="Seller")
    contract, _item = _buyerless_contract_priced_without_inputs(db_session, dealership.id, dealership.dealer_group_id)
    headers = _as(seller)
    before = client.get(f"/v1/sales/contracts/{contract.id}", headers=headers).json()

    taken = client.patch(
        f"/v1/sales/contracts/{contract.id}",
        json={"ownerUserId": seller["id"]},
        headers={**headers, "If-Match": str(before["version"])},
    )

    assert taken.status_code == 200, taken.text
    body = taken.json()
    assert body["ownerUserId"] == seller["id"] and body["ownerLabel"] == "Sara Seller"
    assert body["version"] == before["version"] + 1
    figures = ("grossPrice", "listPrice", "payable", "vehicleSnapshotFrozenAt", "customerId", "vehicleLabel")
    assert {k: body[k] for k in figures} == {k: before[k] for k in figures}

    # A seller sent with any other field is still the KAN-67 refusal.
    refused = client.patch(
        f"/v1/sales/contracts/{contract.id}",
        json={"ownerUserId": None, "manualBasePrice": "1.00"},
        headers={**headers, "If-Match": str(body["version"])},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "contract_priced_without_inputs"
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).owner_user_id == uuid.UUID(seller["id"])
