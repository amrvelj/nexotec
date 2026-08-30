"""WP-8 PR-1: SalesOffer/SalesContract/deal-grid API surface."""

import uuid

from app.core.auth import AccessRole, create_access_token


def _token(role: AccessRole | None = None, is_dealer_manager: bool = False) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


def test_create_contract_direct(client):
    token = _token(role=AccessRole.SALES)
    created = client.post("/v1/sales/contracts", json={}, headers=_bearer(token))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["offerId"] is None


def test_create_contract_from_offer(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    created = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["offerId"] == offer["id"]
    assert body["offerNumber"] == offer["offerNumber"]


def test_deal_grid_shows_one_row_per_lineage(client):
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_bearer(token))
    client.post("/v1/sales/offers", headers=_bearer(token))

    response = client.get("/v1/sales/deals", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    entity_types = sorted(item["entityType"] for item in body["items"])
    assert entity_types == ["contract", "offer"]


def test_deal_grid_unknown_sort_field_is_422(client):
    token = _token(role=AccessRole.SALES)
    response = client.get("/v1/sales/deals?sort=notAField:asc", headers=_bearer(token))
    assert response.status_code == 422, response.text
