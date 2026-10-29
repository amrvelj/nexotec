"""KAN-492 — `OfferRead` names the offer's live contract (`liveContractId`,
`liveContractNumber`): the contract that is not cancelled (KAN-147 keeps it
to one). While it exists the offer is read-only (KAN-408), so the screen
disables the seller picker and «Übernehmen» before the click (ADR-061)
instead of showing the 409 `offer_has_contract` after it."""

from sqlalchemy import event

from tests.identity_support import create_dealership, create_user
from tests.test_sales_owner import _as
from tests.test_sales_seller_only_write import _open_unassigned_offer


def test_an_offer_without_a_contract_names_none(client, db_session):
    seller = create_user(client, create_dealership(client))
    offer = _open_unassigned_offer(client, db_session, seller)

    assert offer["liveContractId"] is None
    assert offer["liveContractNumber"] is None


def test_the_offer_names_its_live_contract_and_frees_it_on_cancellation(client, db_session):
    seller = create_user(client, create_dealership(client))
    offer = _open_unassigned_offer(client, db_session, seller)
    contract = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(seller)).json()

    read = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()
    assert read["liveContractId"] == contract["id"]
    assert read["liveContractNumber"] == contract["contractNumber"]
    # what the field announces is the refusal the write gets
    refused = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"ownerUserId": seller["id"]},
        headers={**_as(seller), "If-Match": str(read["version"])},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["contractNumber"] == read["liveContractNumber"]

    cancelled = client.post(
        f"/v1/sales/contracts/{contract['id']}/cancel",
        json={"reason": "Kunde verzichtet"},
        headers={**_as(seller), "If-Match": str(contract["version"])},
    )
    assert cancelled.status_code == 200, cancelled.text
    freed = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()
    assert freed["liveContractId"] is None
    assert freed["liveContractNumber"] is None


def test_the_offer_list_names_each_offers_live_contract_in_one_query(client, db_session, engine):
    seller = create_user(client, create_dealership(client))
    contracted = _open_unassigned_offer(client, db_session, seller)
    free = _open_unassigned_offer(client, db_session, seller)
    contract = client.post("/v1/sales/contracts", json={"offerId": contracted["id"]}, headers=_as(seller)).json()

    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        items = client.get("/v1/sales/offers", headers=_as(seller)).json()["items"]
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    by_id = {item["id"]: item for item in items}
    assert by_id[contracted["id"]]["liveContractNumber"] == contract["contractNumber"]
    assert by_id[contracted["id"]]["liveContractId"] == contract["id"]
    assert by_id[free["id"]]["liveContractId"] is None
    assert len([s for s in statements if "FROM sales_contract" in s]) == 1


def test_an_offer_with_a_live_contract_generates_no_document_until_the_contract_is_cancelled(client, db_session):
    """KAN-495 (Anto, 2026-10-29, option a): the contract is the live record
    (ADR-083), so a fresh offer document is refused like every other offer
    write (KAN-408) — and allowed again once the contract is cancelled."""
    seller = create_user(client, create_dealership(client))
    offer = _open_unassigned_offer(client, db_session, seller)
    contract = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(seller)).json()

    refused = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_as(seller))
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["reason"] == "offer_has_contract"
    assert details["contractNumber"] == contract["contractNumber"]
    listed = client.get(f"/v1/sales/offers/{offer['id']}/documents", headers=_as(seller)).json()["items"]
    assert listed == []

    cancelled = client.post(
        f"/v1/sales/contracts/{contract['id']}/cancel",
        json={"reason": "Kunde verzichtet"},
        headers={**_as(seller), "If-Match": str(contract["version"])},
    )
    assert cancelled.status_code == 200, cancelled.text
    generated = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_as(seller))
    assert generated.status_code == 201, generated.text
