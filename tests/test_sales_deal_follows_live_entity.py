"""KAN-408 and KAN-409 (Anto, 2026-10-21, option A both times): a deal row is
projected from its deal's LIVE entity.

KAN-408 — once a contract is made from an offer, the offer is read-only (409
`offer_has_contract`): the contract is the live record and is edited itself
while pending. The contract's row keeps the contract's buyer, car and
documents, whatever reaches the projection from the offer's side.

KAN-409 — a cancelled contract frees its offer (KAN-147), and the row turns
back into the offer: its own number, status and seller, so the Sales grid,
the seller filter and the unassigned queue find it. The cancelled contract
stays on the offer's and the customer's history.
"""

import uuid
from decimal import Decimal

import pytest

from app.sales.models.deal import SalesDeal
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from tests.identity_support import PasswordLogin, create_dealership, create_user
from tests.test_user_deactivation import (
    _as,
    _customer_for,
    _offer_for,
    _real_dealer_groups,  # noqa: F401 — autouse: tokens carry the real dealer group
    deactivate,
)


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def _patch_offer(client, user: dict, offer_id: str, body: dict):
    current = client.get(f"/v1/sales/offers/{offer_id}", headers=_as(user)).json()
    return client.patch(
        f"/v1/sales/offers/{offer_id}", json=body, headers={**_as(user), "If-Match": str(current["version"])}
    )


def _patch_contract(client, user: dict, contract_id: str, body: dict):
    current = client.get(f"/v1/sales/contracts/{contract_id}", headers=_as(user)).json()
    return client.patch(
        f"/v1/sales/contracts/{contract_id}", json=body, headers={**_as(user), "If-Match": str(current["version"])}
    )


def _cancel(client, user: dict, contract_id: str):
    current = client.get(f"/v1/sales/contracts/{contract_id}", headers=_as(user)).json()
    response = client.post(
        f"/v1/sales/contracts/{contract_id}/cancel",
        json={"reason": "financing fell through"},
        headers={**_as(user), "If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _refused_as_contracted(response, contract: dict) -> None:
    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "offer_has_contract"
    assert details["contractNumber"] == contract["contractNumber"]


def _deal(client, user: dict, deal_id: str) -> dict:
    rows = client.get("/v1/sales/deals?limit=100", headers=_as(user)).json()["items"]
    return next(d for d in rows if d["id"] == deal_id)


def _offer_with_buyer_and_contract(client):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, firstName="Sara", lastName="Seller")
    anna = _customer_for(client, seller, firstName="Anna")
    patched = _patch_offer(
        client,
        seller,
        _offer_for(client, seller)["id"],
        {
            "customerId": anna["id"],
            "vehicleSource": "manual",
            "vehicleLabel": "Škoda Octavia",
            "manualBasePrice": "30000.00",
        },
    )
    assert patched.status_code == 200, patched.text
    offer = patched.json()
    # One document on the offer before the contract: the contract's row counts it with its own.
    document = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_as(seller))
    assert document.status_code == 201, document.text
    contract = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(seller))
    assert contract.status_code == 201, contract.text
    return dealership, seller, offer, contract.json()


# --- KAN-408 ------------------------------------------------------------------


def test_an_offer_with_a_live_contract_refuses_every_edit_and_its_deal_row_stays_the_contracts(client):
    _dealership, seller, offer, contract = _offer_with_buyer_and_contract(client)
    beat = _customer_for(client, seller, firstName="Beat")
    carla = _customer_for(client, seller, firstName="Carla")
    # The contract is edited itself while pending (ADR-083): its own buyer and car.
    edited = _patch_contract(client, seller, contract["id"], {"customerId": beat["id"], "vehicleLabel": "Cupra Born"})
    assert edited.status_code == 200, edited.text
    printed = client.post(f"/v1/sales/contracts/{contract['id']}/documents", headers=_as(seller))
    assert printed.status_code == 201, printed.text
    assert _deal(client, seller, offer["id"])["documentsCount"] == 2  # generating a document re-projects

    _refused_as_contracted(_patch_offer(client, seller, offer["id"], {"customerId": carla["id"]}), contract)
    current = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()
    line_items = client.put(
        f"/v1/sales/offers/{offer['id']}/line-items",
        json={},
        headers={**_as(seller), "If-Match": str(current["version"])},
    )
    _refused_as_contracted(line_items, contract)
    finalize = client.post(
        f"/v1/sales/offers/{offer['id']}/finalize", headers={**_as(seller), "If-Match": str(current["version"])}
    )
    _refused_as_contracted(finalize, contract)
    cancel = client.post(
        f"/v1/sales/offers/{offer['id']}/cancel",
        json={"reason": "duplicate"},
        headers={**_as(seller), "If-Match": str(current["version"])},
    )
    _refused_as_contracted(cancel, contract)

    deal = _deal(client, seller, offer["id"])
    assert deal["entityType"] == "contract" and deal["contractId"] == contract["id"]
    assert deal["customerId"] == beat["id"]
    assert deal["vehicleLabel"] == "Cupra Born"
    assert deal["documentsCount"] == 2  # the offer's and the contract's
    offer_now = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()
    assert offer_now["customerId"] != carla["id"] and offer_now["version"] == current["version"]


def test_the_offer_side_of_the_projection_reprojects_the_live_contract(client, db_session):
    """A write on the offer's side that is not a user's edit (a document
    rendered for it, a customer merge repointing it) still reaches the
    projection with the offer: the contract's row is projected from the
    contract, never from the offer's labels."""

    _dealership, seller, offer, contract = _offer_with_buyer_and_contract(client)
    beat = _customer_for(client, seller, firstName="Beat")
    assert _patch_contract(client, seller, contract["id"], {"customerId": beat["id"]}).status_code == 200
    # A second offer document after the contract exists (rendering is not an edit): counted on the contract's row.
    assert client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_as(seller)).status_code == 201
    assert _deal(client, seller, offer["id"])["documentsCount"] == 2

    row = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    row.customer_label = "Somebody Else"
    row.vehicle_label = "Another Car"
    upsert_deal_projection(db_session, offer=row)
    db_session.commit()

    deal = db_session.get(SalesDeal, uuid.UUID(offer["id"]))
    assert deal.entity_type == "contract" and deal.contract_id == uuid.UUID(contract["id"])
    assert deal.customer_id == uuid.UUID(beat["id"])
    assert deal.customer_label != "Somebody Else"
    assert deal.vehicle_label == "Škoda Octavia"
    assert deal.documents_count == 2


def test_a_cancelled_contract_makes_its_offer_editable_again(client):
    _dealership, seller, offer, contract = _offer_with_buyer_and_contract(client)
    _cancel(client, seller, contract["id"])

    response = _patch_offer(client, seller, offer["id"], {"manualBasePrice": "41000.00"})
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["manualBasePrice"]) == Decimal("41000.00")


# --- KAN-409 ------------------------------------------------------------------


def _open(db_session, offer_id: str) -> None:
    """Finalize needs a priced vehicle and a document; the grid reads only the
    status, so it is set as finalize sets it and re-projected."""

    offer = db_session.get(SalesOffer, uuid.UUID(offer_id))
    offer.status = OfferStatus.OPEN
    upsert_deal_projection(db_session, offer=offer)
    db_session.commit()


def test_a_cancelled_contract_hands_the_row_back_to_its_offer(client, db_session):
    _dealership, seller, offer, contract = _offer_with_buyer_and_contract(client)
    _open(db_session, offer["id"])
    beat = _customer_for(client, seller, firstName="Beat")
    assert _patch_contract(client, seller, contract["id"], {"customerId": beat["id"]}).status_code == 200

    _cancel(client, seller, contract["id"])

    deal = _deal(client, seller, offer["id"])
    assert deal["entityType"] == "offer"
    assert deal["number"] == offer["offerNumber"]
    assert deal["status"] == "open"
    assert deal["contractId"] is None and deal["contractNumber"] is None
    assert deal["customerId"] == offer["customerId"] != beat["id"]
    assert deal["vehicleLabel"] == "Škoda Octavia"
    assert deal["margin"] is None
    assert deal["ownerUserId"] == seller["id"]
    # The cancelled contract stays on the customer's and the offer's history.
    history = client.get(f"/v1/sales/contracts?customer_id={beat['id']}", headers=_as(seller)).json()["items"]
    assert [(c["id"], c["status"]) for c in history] == [(contract["id"], "cancelled")]


def _freed_offer(client, db_session, leaver: dict, buyer: dict) -> dict:
    offer = _offer_for(client, leaver)
    assert _patch_offer(client, leaver, offer["id"], {"customerId": buyer["id"]}).status_code == 200
    _open(db_session, offer["id"])
    contract = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(leaver))
    assert contract.status_code == 201, contract.text
    _cancel(client, leaver, contract.json()["id"])
    return offer


def test_a_freed_offer_is_found_in_the_queue_and_under_its_heir_after_a_hand_over(client, db_session, login):
    """cancel → freed offer → hand-over → queue: one leaver's freed offer
    handed to nobody is in the unassigned queue; another's handed to the heir
    is under the heir's seller filter, named for the heir."""

    dealership = create_dealership(client)
    to_queue = create_user(client, dealership, isDealerManager=False, firstName="Lea", lastName="Ver")
    to_heir = create_user(client, dealership, isDealerManager=False, firstName="Max", lastName="Weg")
    heir = create_user(client, dealership, isDealerManager=False, firstName="Hans", lastName="Erbe")
    login.accept_invitation(heir)
    create_user(client, dealership)
    queued = _freed_offer(client, db_session, to_queue, _customer_for(client, to_queue))
    handed = _freed_offer(client, db_session, to_heir, _customer_for(client, to_heir))

    for leaver, to_user in ((to_queue, None), (to_heir, heir["id"])):
        response = deactivate(
            client,
            dealership,
            leaver,
            transfers=[
                {"module": "customer", "group": "customers", "toUserId": heir["id"]},
                {"module": "sales", "group": "offers", "toUserId": to_user},
            ],
        )
        assert response.status_code == 200, response.text

    queue = client.get("/v1/sales/deals?unassigned=true", headers=_as(heir)).json()["items"]
    assert [(d["offerId"], d["entityType"], d["contractId"]) for d in queue] == [(queued["id"], "offer", None)]
    mine = client.get(f"/v1/sales/deals?ownerUserId={heir['id']}", headers=_as(heir)).json()["items"]
    assert [(d["offerId"], d["entityType"], d["ownerLabel"]) for d in mine] == [(handed["id"], "offer", "Hans Erbe")]


# --- KAN-408 criterion 3: the nightly check (rule 10) -------------------------


def _deal_row_findings(db_session) -> dict[str, set[uuid.UUID]]:
    from app.core.reconciliation import find_rows_in_state
    from app.sales.reconciliation import deal_row_checks

    db_session.expire_all()
    return {
        ("contract" if "from its contract" in check.label else "offer"): {
            row for row, _ in find_rows_in_state(db_session, check)
        }
        for check in deal_row_checks()
    }


def test_the_nightly_check_finds_no_deal_row_on_projected_data(client, db_session):
    _dealership, seller, _offer, _contract = _offer_with_buyer_and_contract(client)
    freed = _offer_for(client, seller)
    freed_contract = client.post("/v1/sales/contracts", json={"offerId": freed["id"]}, headers=_as(seller)).json()
    _cancel(client, seller, freed_contract["id"])

    assert _deal_row_findings(db_session) == {"contract": set(), "offer": set()}


def test_the_nightly_check_reports_a_deal_row_that_differs_from_its_live_entity(client, db_session):
    _dealership, seller, offer, _contract = _offer_with_buyer_and_contract(client)
    wrong_label = db_session.get(SalesDeal, uuid.UUID(offer["id"]))
    wrong_label.customer_label = "Somebody Else"  # as the pre-KAN-408 offer branch wrote it
    db_session.commit()

    # Only the count wrong: an offer-side save after the contract existed wrote
    # the offer's own count onto the contract's row (before KAN-408).
    counted_wrong = _offer_with_buyer_and_contract(client)[2]
    db_session.get(SalesDeal, uuid.UUID(counted_wrong["id"])).documents_count = 0
    db_session.commit()

    # A row left in a cancelled contract's shape (written before KAN-409).
    freed = _offer_for(client, seller)
    freed_contract = client.post("/v1/sales/contracts", json={"offerId": freed["id"]}, headers=_as(seller)).json()
    _cancel(client, seller, freed_contract["id"])
    stale_shape = db_session.get(SalesDeal, uuid.UUID(freed["id"]))
    stale_shape.entity_type = "contract"
    stale_shape.contract_id = uuid.UUID(freed_contract["id"])
    stale_shape.contract_number = freed_contract["contractNumber"]
    stale_shape.number = freed_contract["contractNumber"]
    stale_shape.status = "cancelled"
    db_session.commit()

    # An offer's row while the offer has a live contract.
    shown_as_offer = _offer_for(client, seller)
    client.post("/v1/sales/contracts", json={"offerId": shown_as_offer["id"]}, headers=_as(seller))
    db_session.expire_all()
    row = db_session.get(SalesDeal, uuid.UUID(shown_as_offer["id"]))
    row.entity_type, row.contract_id, row.contract_number = "offer", None, None
    row.number, row.status = shown_as_offer["offerNumber"], "draft"
    db_session.commit()

    assert _deal_row_findings(db_session) == {
        "contract": {uuid.UUID(offer["id"]), uuid.UUID(freed["id"]), uuid.UUID(counted_wrong["id"])},
        "offer": {uuid.UUID(shown_as_offer["id"])},
    }


def test_the_backfill_reprojects_a_row_left_in_a_cancelled_contracts_shape(client, db_session):
    """scripts/backfill_sales_deal_projection.py: the dry run changes nothing
    and names the rows; --commit hands the cancelled contract's row back to
    its offer and recounts a contract row's documents; a second run finds
    nothing."""

    from scripts.backfill_sales_deal_projection import reproject

    _dealership, seller, counted_wrong, _contract = _offer_with_buyer_and_contract(client)
    db_session.get(SalesDeal, uuid.UUID(counted_wrong["id"])).documents_count = 0
    db_session.commit()
    freed = _offer_for(client, seller)
    freed_contract = client.post("/v1/sales/contracts", json={"offerId": freed["id"]}, headers=_as(seller)).json()
    _cancel(client, seller, freed_contract["id"])
    stale = db_session.get(SalesDeal, uuid.UUID(freed["id"]))
    stale.entity_type, stale.status = "contract", "cancelled"
    stale.contract_id = uuid.UUID(freed_contract["id"])
    stale.number = stale.contract_number = freed_contract["contractNumber"]
    db_session.commit()

    done, remaining = reproject(db_session, commit=False)
    assert {(r.deal_id, r.after.split(" ")[0]) for r in done} == {
        (uuid.UUID(freed["id"]), "offer"),
        (uuid.UUID(counted_wrong["id"]), "contract"),
    }
    assert remaining == []
    db_session.expire_all()
    assert db_session.get(SalesDeal, uuid.UUID(freed["id"])).entity_type == "contract"
    assert db_session.get(SalesDeal, uuid.UUID(counted_wrong["id"])).documents_count == 0

    done, remaining = reproject(db_session, commit=True)
    assert len(done) == 2 and remaining == []
    db_session.expire_all()
    row = db_session.get(SalesDeal, uuid.UUID(freed["id"]))
    assert (row.entity_type, row.number, row.contract_id) == ("offer", freed["offerNumber"], None)
    assert db_session.get(SalesDeal, uuid.UUID(counted_wrong["id"])).documents_count == 1
    assert reproject(db_session, commit=True) == ([], [])
