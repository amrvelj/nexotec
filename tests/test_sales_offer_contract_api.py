"""WP-8 PR-1: SalesOffer/SalesContract/deal-grid API surface."""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.sales.models.contract import SalesContract
from tests.test_sales_contract_edit import app_sessions_on_the_test_engine  # noqa: F401 - used by name


def _token(role: AccessRole | None = None, is_dealer_manager: bool = False, tenant_id: uuid.UUID | None = None) -> str:
    tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealership(client) -> str:
    """KAN-58's customer-attached contract tests need a real Dealership row
    — POST /v1/customers 404s on get_dealership_or_404 otherwise (the
    tenant_id claim proving the caller's session is real)."""

    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG", "dealerLicenseNumber": "ZH-12345", "licenseState": "ZH",
        "franchiseType": "independent",
        "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
        "phone": "+41441234567", "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _buyer(client, token: str, **overrides) -> dict:
    """A customer with a usable domicile address — what a contract needs to
    be confirmed (D-20; a buyer at all since KAN-307). The token's dealership
    must exist (_create_dealership)."""

    payload = {
        "firstName": "Ursula", "lastName": "Vogt", "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"ursula-{uuid.uuid4().hex[:6]}@example.ch"}],
        "addresses": [{
            "addressType": "domicile", "addressStreet": "Bahnhofstrasse", "addressHouseNumber": "1",
            "addressPostalCode": "8001", "addressLocality": "Zürich", "addressCountry": "CH", "isPrimary": True,
        }],
        **overrides,
    }
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_offer_requires_write_capability(client):
    token = _token()  # no roles, not a manager
    response = client.post("/v1/sales/offers", headers=_bearer(token))
    assert response.status_code == 403, response.text


def test_create_and_get_offer(client):
    token = _token(role=AccessRole.SALES)
    created = client.post("/v1/sales/offers", headers=_bearer(token))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    assert body["offerNumber"].startswith("O-")

    fetched = client.get(f"/v1/sales/offers/{body['id']}", headers=_bearer(token))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == body["id"]


def test_create_offer_cross_tenant_get_is_404_not_403(client):
    owner = _token(role=AccessRole.SALES)
    other = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(owner)).json()

    response = client.get(f"/v1/sales/offers/{offer['id']}", headers=_bearer(other))
    assert response.status_code == 404, response.text


def test_cancel_offer_requires_if_match(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.post(f"/v1/sales/offers/{offer['id']}/cancel", json={"reason": "Test"}, headers=_bearer(token))
    assert response.status_code == 400, response.text


def test_cancel_offer(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.post(
        f"/v1/sales/offers/{offer['id']}/cancel",
        json={"reason": "Kunde hat abgesagt."},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"


def test_a_contract_with_neither_an_offer_nor_a_customer_is_refused(client, db_session):
    """KAN-276 (Anto, 2026-10-11: "no empty contracts"): a contract is created
    from an offer, for a customer or (KAN-307) for a car, never from nothing."""

    token = _token(role=AccessRole.SALES)

    for body in ({}, {"offerId": None, "customerId": None, "stockItemId": None}):
        response = client.post("/v1/sales/contracts", json=body, headers=_bearer(token))
        assert response.status_code == 422, response.text
        assert "offerId, customerId or stockItemId" in response.text

    assert db_session.query(SalesContract).count() == 0


def test_create_contract_from_offer(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    created = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["offerId"] == offer["id"]
    assert body["offerNumber"] == offer["offerNumber"]


# --- KAN-58: POST /sales/contracts { customerId } — "New contract" from the
# customer's own surfaces, with no offer at all. ---------------------------


def test_create_contract_for_a_customer(client):
    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))
    customer = client.post(
        "/v1/customers",
        json={
            "firstName": "Ursula", "lastName": "Vogt", "language": "fr",
            "emails": [{"emailType": "personal", "emailAddress": "ursula@example.ch"}],
        },
        headers=_bearer(token),
    ).json()

    created = client.post("/v1/sales/contracts", json={"customerId": customer["id"]}, headers=_bearer(token))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["offerId"] is None
    assert body["customerId"] == customer["id"]
    assert body["customerLabel"] == "Ursula Vogt"


def test_confirming_a_contract_created_for_a_customer_with_no_vehicle_is_409(client):
    """KAN-66 / G-67, reproduced live on staging 2026-09-21: the contract
    above used to confirm (200) with every amount null. It must now refuse."""

    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))
    customer = client.post(
        "/v1/customers",
        json={
            "firstName": "Ursula", "lastName": "Vogt", "language": "fr",
            "emails": [{"emailType": "personal", "emailAddress": "ursula.vogt@example.ch"}],
            "addresses": [
                {"addressType": "domicile", "addressStreet": "Bahnhofstrasse", "addressHouseNumber": "1",
                 "addressPostalCode": "8001", "addressLocality": "Zürich", "addressCountry": "CH", "isPrimary": True}
            ],
        },
        headers=_bearer(token),
    ).json()
    created = client.post("/v1/sales/contracts", json={"customerId": customer["id"]}, headers=_bearer(token)).json()

    response = client.post(
        f"/v1/sales/contracts/{created['id']}/confirm", headers={**_bearer(token), "If-Match": str(created["version"])}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "missing_vehicle"


def test_create_contract_for_a_do_not_contact_customer_is_409(client):
    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))
    customer = client.post(
        "/v1/customers",
        json={
            "firstName": "Beat", "lastName": "Frei", "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": "beat@example.ch"}],
        },
        headers=_bearer(token),
    ).json()
    client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lifecycleStatus": "do_not_contact"},
        headers={**_bearer(token), "If-Match": str(customer["version"])},
    )

    response = client.post("/v1/sales/contracts", json={"customerId": customer["id"]}, headers=_bearer(token))
    assert response.status_code == 409, response.text


def test_create_contract_offer_and_customer_together_is_422(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    response = client.post(
        "/v1/sales/contracts",
        json={"offerId": offer["id"], "customerId": str(uuid.uuid4())},
        headers=_bearer(token),
    )
    assert response.status_code == 422, response.text


def test_deal_grid_shows_one_row_per_lineage(client):
    """A draft offer converting to a contract updates ONE row in place —
    never leaves a second, stale offer row behind. The second, still-draft
    offer created here is deliberately excluded from the count: draft
    offers don't show in the overview at all (see
    test_deal_grid_excludes_draft_offers below) — this test's own job is
    the dedup property, not a row count that happens to include one.
    """

    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    client.post("/v1/sales/offers", headers=_bearer(token))  # a second, still-draft offer

    response = client.get("/v1/sales/deals", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    entity_types = sorted(item["entityType"] for item in body["items"])
    assert entity_types == ["contract"]


def test_deal_grid_excludes_draft_offers(client):
    """A contract that's aborted mid-flow (or hasn't been started from an
    offer at all) sits in `pending` and stays visible — only a DRAFT
    offer is excluded, an offer never yet opened for real work.
    """

    token = _token(role=AccessRole.SALES)
    client.post("/v1/sales/offers", headers=_bearer(token))  # stays draft, never touched further

    response = client.get("/v1/sales/deals", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


def test_deal_grid_shows_pending_contracts(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    created = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    assert created.status_code == 201, created.text  # pending

    response = client.get("/v1/sales/deals", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "pending"


def test_deal_grid_unknown_sort_field_is_422(client):
    token = _token(role=AccessRole.SALES)
    response = client.get("/v1/sales/deals?sort=notAField:asc", headers=_bearer(token))
    assert response.status_code == 422, response.text


def test_patch_offer_manual_vehicle_updates_containers(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    assert offer["containers"][1]["id"] == "vehicle"
    assert offer["containers"][1]["status"] == "not_started"

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"vehicleSource": "manual", "vehicleLabel": "Volkswagen Käfer", "manualVehicleCondition": "used"},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["vehicleLabel"] == "Volkswagen Käfer"
    vehicle_container = next(c for c in body["containers"] if c["id"] == "vehicle")
    assert vehicle_container["status"] == "complete"


def test_patch_offer_requires_if_match(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    response = client.patch(f"/v1/sales/offers/{offer['id']}", json={"vehicleLabel": "X"}, headers=_bearer(token))
    assert response.status_code == 400, response.text


def test_request_invoice_for_a_vehicle_not_purchased_is_409_with_its_reason(client):
    """KAN-100 (Anto, 2026-10-04): a contract is confirmed before the
    purchase; invoicing is not. A manually configured vehicle is the case
    Sales can never see purchased today, so it refuses over HTTP too."""

    token = _token(role=AccessRole.SALES, tenant_id=uuid.UUID(_create_dealership(client)))
    buyer = _buyer(client, token)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    offer = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={
            "customerId": buyer["id"],
            "vehicleSource": "manual", "vehicleLabel": "Volkswagen ID.4 Pro", "manualVehicleCondition": "new",
            "manualBasePrice": "52000.00",
        },
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    ).json()
    contract = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token)).json()
    assert contract["isInvoiceable"] is False
    confirmed = client.post(
        f"/v1/sales/contracts/{contract['id']}/confirm", headers={**_bearer(token), "If-Match": str(contract["version"])}
    )
    assert confirmed.status_code == 200, confirmed.text

    response = client.post(
        f"/v1/sales/contracts/{contract['id']}/request-invoice",
        headers={**_bearer(token), "If-Match": str(confirmed.json()["version"])},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "vehicle_not_purchased"}


def test_an_offer_has_one_live_contract_until_that_one_is_cancelled(client):
    """KAN-147: a second contract from the same offer would sell the deal
    twice (and, for a legacy sale's synthesised offer, invoice it again).
    Cancelling the first frees the offer."""

    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    first = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token)).json()
    assert first["isLegacy"] is False

    second = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))

    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"] == {
        "reason": "offer_already_contracted", "contractNumber": first["contractNumber"]
    }

    cancelled = client.post(
        f"/v1/sales/contracts/{first['id']}/cancel", json={"reason": "Kunde verzichtet"},
        headers={**_bearer(token), "If-Match": str(first["version"])},
    )
    assert cancelled.status_code == 200, cancelled.text
    again = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    assert again.status_code == 201, again.text


# --- KAN-307: POST /sales/contracts { stockItemId } — "Vertrag erstellen" on a
# stock car starts the contract with that car, like "Offerte erstellen". ------


def test_create_contract_for_a_stock_car(client, db_session):
    from tests.test_sales_lifecycle_reservation import _dealership
    from tests.test_sales_offer_refinements import _stock_item

    tenant_id = _dealership(db_session).id
    item = _stock_item(db_session, tenant_id)
    token = _token(role=AccessRole.SALES, tenant_id=tenant_id)

    created = client.post(
        "/v1/sales/contracts",
        json={"stockItemId": str(item.id), "vehicleLabel": item.vehicle_label},
        headers=_bearer(token),
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["status"], body["customerId"], body["offerId"]) == ("pending", None, None)
    assert (body["vehicleSource"], body["stockItemId"], body["vehicleLabel"]) == ("stock", str(item.id), item.vehicle_label)
    # Priced through the contract's own Path A edit, as its PATCH would.
    assert body["grossPrice"] is not None
    containers = {c["id"]: c["status"] for c in body["containers"]}
    assert (containers["vehicle"], containers["customer"]) == ("complete", "not_started")


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_a_contract_started_from_a_car_gets_its_buyer_in_the_contract_and_is_then_confirmed(client, db_session):
    """KAN-307 (Anto, 2026-10-12, amending ADR-083): as on the offer, the
    buyer is picked inside the pending contract. Without one the contract
    cannot be confirmed (409 missing_customer); with one it can."""

    from tests.test_sales_lifecycle_reservation import _dealership
    from tests.test_sales_offer_refinements import _stock_item

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    token = _token(role=AccessRole.SALES, tenant_id=dealership.id)
    buyer = _buyer(client, token)
    contract = client.post(
        "/v1/sales/contracts", json={"stockItemId": str(item.id), "vehicleLabel": item.vehicle_label},
        headers=_bearer(token),
    ).json()

    unsigned = client.post(
        f"/v1/sales/contracts/{contract['id']}/confirm", headers={**_bearer(token), "If-Match": str(contract["version"])}
    )
    assert unsigned.status_code == 409, unsigned.text
    assert unsigned.json()["error"]["details"] == {"reason": "missing_customer"}

    with_buyer = client.patch(
        f"/v1/sales/contracts/{contract['id']}", json={"customerId": buyer["id"]},
        headers={**_bearer(token), "If-Match": str(contract["version"])},
    )
    assert with_buyer.status_code == 200, with_buyer.text
    body = with_buyer.json()
    assert (body["customerId"], body["customerLabel"], body["version"]) == (buyer["id"], "Ursula Vogt", contract["version"] + 1)
    assert {c["id"]: c["status"] for c in body["containers"]}["customer"] == "complete"

    confirmed = client.post(
        f"/v1/sales/contracts/{contract['id']}/confirm", headers={**_bearer(token), "If-Match": str(body["version"])}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    # A signed contract's buyer is frozen with the rest of it.
    frozen = client.patch(
        f"/v1/sales/contracts/{contract['id']}", json={"customerId": None},
        headers={**_bearer(token), "If-Match": str(confirmed.json()["version"])},
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["details"]["reason"] == "contract_not_pending"


def test_a_do_not_contact_customer_cannot_become_a_contracts_buyer(client, db_session):
    from tests.test_sales_lifecycle_reservation import _dealership
    from tests.test_sales_offer_refinements import _stock_item

    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    token = _token(role=AccessRole.SALES, tenant_id=dealership.id)
    blocked = _buyer(client, token, lifecycleStatus="do_not_contact")
    contract = client.post(
        "/v1/sales/contracts", json={"stockItemId": str(item.id)}, headers=_bearer(token),
    ).json()

    response = client.patch(
        f"/v1/sales/contracts/{contract['id']}", json={"customerId": blocked["id"]},
        headers={**_bearer(token), "If-Match": str(contract["version"])},
    )

    assert response.status_code == 409, response.text
    assert "do-not-contact" in response.json()["error"]["message"]
    assert client.get(f"/v1/sales/contracts/{contract['id']}", headers=_bearer(token)).json()["customerId"] is None


def test_a_contract_for_an_unknown_stock_car_is_404_and_leaves_no_contract(client, db_session):
    token = _token(role=AccessRole.SALES)

    response = client.post(
        "/v1/sales/contracts", json={"stockItemId": str(uuid.uuid4())}, headers=_bearer(token)
    )

    assert response.status_code == 404, response.text
    assert db_session.query(SalesContract).count() == 0


def test_a_stock_car_cannot_be_combined_with_an_offer_or_a_customer(client, db_session):
    token = _token(role=AccessRole.SALES)
    car = str(uuid.uuid4())

    for other in ({"offerId": str(uuid.uuid4())}, {"customerId": str(uuid.uuid4())}):
        response = client.post("/v1/sales/contracts", json={"stockItemId": car, **other}, headers=_bearer(token))
        assert response.status_code == 422, response.text
    label_alone = client.post("/v1/sales/contracts", json={"customerId": car, "vehicleLabel": "Golf"}, headers=_bearer(token))
    assert label_alone.status_code == 422, label_alone.text

    assert db_session.query(SalesContract).count() == 0
