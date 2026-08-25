"""Grid footer row counts (U-07): exact under the threshold, capped
"at least N" estimate above it, never a full scan on a filtered table.
"""

import uuid

from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.pagination import count_capped
from app.customer.models.customer import Customer

VALID_ADDRESS = {
    "street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH",
}


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG", "dealerLicenseNumber": "ZH-12345", "licenseState": "ZH",
        "franchiseType": "independent", "address": VALID_ADDRESS, "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna", "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "private", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def test_count_capped_exact_under_threshold(db_session):
    stmt = select(Customer)
    n, is_estimate = count_capped(db_session, stmt, threshold=10)
    assert n == 0
    assert is_estimate is False


def test_count_capped_estimate_above_threshold(client, db_session):
    dealer_id = _create_dealer(client)
    for _ in range(5):
        _create_customer(client, dealer_id)

    stmt = select(Customer).where(Customer.tenant_id == uuid.UUID(dealer_id))
    n, is_estimate = count_capped(db_session, stmt, threshold=3)
    assert n == 3
    assert is_estimate is True


def test_list_customers_reports_exact_total(client):
    dealer_id = _create_dealer(client)
    for _ in range(4):
        _create_customer(client, dealer_id)

    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4
    assert body["totalIsEstimate"] is False


def test_list_customers_total_respects_filters(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lifecycleStatus="active")
    _create_customer(client, dealer_id, lifecycleStatus="active")
    _create_customer(client, dealer_id, lifecycleStatus="prospect")

    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params={"lifecycle_status": "active"}, headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
