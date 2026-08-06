import random
import uuid

import pytest

from app.core.auth import AccessRole, create_access_token

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(role: AccessRole, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> str:
    return create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id or uuid.uuid4(), access_role=role
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Sam",
        "lastName": "Sales",
        "email": f"sam-{uuid.uuid4().hex[:8]}@example.ch",  # User.email is globally unique
        "role": "sales",
        "accessRole": "sales",
        "authIdentityId": "stub-sub-1",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealers/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"firstName": "Peter", "lastName": "Beispiel", "email": "peter@example.ch"}
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _random_vin() -> str:
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # excludes I, O, Q per ISO 3779
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, **overrides) -> dict:
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _setup(client, dealer_id: str | None = None):
    """Creates a dealer + user + customer + vehicle (custodied by that
    dealer) — the minimal fixture set every Transaction test needs.
    """

    dealer_id = dealer_id or _create_dealer(client)
    user = _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    return dealer_id, user, customer, vehicle


def _transaction_payload(user: dict, customer: dict, vehicle: dict, **overrides):
    payload = {
        "transactionType": "sale",
        "customerId": customer["id"],
        "vehicleId": vehicle["id"],
        "primaryUserId": user["id"],
    }
    payload.update(overrides)
    return payload


def _create_transaction(client, dealer_id: str, user: dict, customer: dict, vehicle: dict, **overrides) -> dict:
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle, **overrides), headers=_bearer(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- creation / access control ------------------------------------------------


def test_dealer_admin_can_create_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    assert body["status"] == "draft"
    assert body["transactionType"] == "sale"
    assert body["transactionDate"] is None
    assert body["version"] == 1


def test_sales_can_create_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", [AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_write_roles_cannot_create_transaction(client, role):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 403


def test_create_transaction_requires_authentication(client):
    dealer_id, user, customer, vehicle = _setup(client)
    response = client.post("/v1/transactions", json=_transaction_payload(user, customer, vehicle))
    assert response.status_code == 401


def test_create_transaction_under_nonexistent_tenant_is_404(client):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(AccessRole.DEALER_ADMIN)  # random tenant_id, no real Dealer row
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 404


def test_customer_from_other_tenant_is_rejected(client):
    dealer_a, user_a, _customer_a, vehicle_a = _setup(client)
    dealer_b, _user_b, customer_b, _vehicle_b = _setup(client)
    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    response = client.post(
        "/v1/transactions",
        json=_transaction_payload(user_a, customer_b, vehicle_a),
        headers=_bearer(token_a),
    )
    assert response.status_code == 404


def test_user_from_other_tenant_is_rejected(client):
    dealer_a, _user_a, customer_a, vehicle_a = _setup(client)
    dealer_b, user_b, _customer_b, _vehicle_b = _setup(client)
    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    response = client.post(
        "/v1/transactions",
        json=_transaction_payload(user_b, customer_a, vehicle_a),
        headers=_bearer(token_a),
    )
    assert response.status_code == 404


def test_nonexistent_vehicle_is_rejected(client):
    dealer_id, user, customer, _vehicle = _setup(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = _transaction_payload(user, customer, {"id": str(uuid.uuid4())})
    response = client.post("/v1/transactions", json=payload, headers=_bearer(token))
    assert response.status_code == 404


# --- update (draft only) --------------------------------------------------------


def test_patch_without_if_match_is_400(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(f"/v1/transactions/{body['id']}", json={"notes": "test"}, headers=_bearer(token))
    assert response.status_code == 400


def test_patch_updates_draft_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/transactions/{body['id']}",
        json={"amount": "42000.00"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["amount"] == "42000.00"
    assert response.json()["version"] == 2


def test_patch_after_completion_is_rejected(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="10000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = _bearer(token)
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**headers, "If-Match": "1"})

    response = client.patch(
        f"/v1/transactions/{body['id']}", json={"notes": "late edit"}, headers={**headers, "If-Match": "2"}
    )
    assert response.status_code == 409


# --- complete: sale --------------------------------------------------------------


def test_complete_sale_requires_amount(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 400


def test_complete_sale_marks_vehicle_sold_and_clears_custodian(client):
    """R1 (PM/CTO ruling, 2026-08-06): the selling dealer still sees the
    resulting `status: "sold"` on their own vehicle after their own sale,
    via the "ever appeared in this vehicle's custody history" signal — even
    though `currentCustodianPartnerId` correctly clears to null (nobody
    currently holds it, and that field stays strictly current-custodian-or-
    platform_admin-only, never leaking who held it after a transfer).
    """

    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="35000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    response = client.post(
        f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["transactionDate"] is not None

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    vehicle_resp = client.get(f"/v1/vehicles/{vehicle['id']}", headers=_bearer(admin_token))
    assert vehicle_resp.json()["status"] == "sold"
    assert vehicle_resp.json()["currentCustodianPartnerId"] is None

    own_dealer_resp = client.get(f"/v1/vehicles/{vehicle['id']}", headers=_bearer(token))
    assert own_dealer_resp.json()["status"] == "sold"
    assert own_dealer_resp.json()["currentCustodianPartnerId"] is None


def test_dealer_with_no_custody_history_still_gets_redacted_status_after_sale(client):
    """R1's other assertion: a dealer who never touched this vehicle stays
    fully redacted even after it's sold — the fix only extends visibility
    to dealers with actual custody history, not to everyone.
    """

    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="35000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"})

    stranger_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant, no custody history
    stranger_resp = client.get(f"/v1/vehicles/{vehicle['id']}", headers=_bearer(stranger_token))
    assert stranger_resp.json()["status"] is None
    assert stranger_resp.json()["currentCustodianPartnerId"] is None


def test_complete_sale_creates_sold_custody_event(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="35000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"})

    events = client.get(f"/v1/vehicles/{vehicle['id']}/custody-events", headers=_bearer(token))
    items = events.json()["items"]
    assert any(e["eventType"] == "sold" and e["transactionId"] == body["id"] for e in items)


def test_cannot_complete_sale_if_dealer_does_not_hold_custody(client):
    dealer_a, user_a, customer_a, vehicle = _setup(client)
    dealer_b = _create_dealer(client)
    # Transfer custody away from dealer_a before completing.
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/vehicles/{vehicle['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(admin_token),
    )

    body = _create_transaction(client, dealer_a, user_a, customer_a, vehicle, amount="1000.00")
    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    response = client.post(
        f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token_a), "If-Match": "1"}
    )
    assert response.status_code == 409


def test_cannot_complete_already_completed_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="1000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = _bearer(token)
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**headers, "If-Match": "1"})

    response = client.post(f"/v1/transactions/{body['id']}/complete", headers={**headers, "If-Match": "2"})
    assert response.status_code == 409


# --- complete: trade_in -----------------------------------------------------------


def test_complete_trade_in_marks_vehicle_in_stock_and_sets_custodian(client):
    dealer_id, user, customer, _vehicle = _setup(client)
    # A trade-in vehicle isn't already custodied by this dealer.
    other_dealer_id = _create_dealer(client)
    trade_in_vehicle = _create_vehicle(client, other_dealer_id, vin="9BWZZZ377VT004251")

    body = _create_transaction(
        client, dealer_id, user, customer, trade_in_vehicle, transactionType="trade_in", amount="8000.00"
    )
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 200, response.text

    vehicle_resp = client.get(f"/v1/vehicles/{trade_in_vehicle['id']}", headers=_bearer(token))
    assert vehicle_resp.json()["status"] == "in_stock"
    assert vehicle_resp.json()["currentCustodianPartnerId"] == dealer_id


# --- cancel ---------------------------------------------------------------------


def test_cancel_draft_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/transactions/{body['id']}/cancel",
        json={"reason": "customer backed out"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_does_not_mutate_vehicle(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/transactions/{body['id']}/cancel", json={}, headers={**_bearer(token), "If-Match": "1"}
    )

    vehicle_resp = client.get(f"/v1/vehicles/{vehicle['id']}", headers=_bearer(token))
    assert vehicle_resp.json()["status"] == "in_transit"
    assert vehicle_resp.json()["currentCustodianPartnerId"] == dealer_id


def test_cannot_cancel_completed_transaction(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="1000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = _bearer(token)
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**headers, "If-Match": "1"})

    response = client.post(
        f"/v1/transactions/{body['id']}/cancel", json={}, headers={**headers, "If-Match": "2"}
    )
    assert response.status_code == 409


# --- cross-tenant isolation ------------------------------------------------------


def test_get_transaction_cross_tenant_is_404(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    other_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant
    response = client.get(f"/v1/transactions/{body['id']}", headers=_bearer(other_token))
    assert response.status_code == 404


def test_list_transactions_is_tenant_scoped(client):
    dealer_a, user_a, customer_a, vehicle_a = _setup(client)
    dealer_b = _create_dealer(client)
    _create_transaction(client, dealer_a, user_a, customer_a, vehicle_a)

    token_b = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))
    response = client.get("/v1/transactions", headers=_bearer(token_b))
    assert response.json()["items"] == []


def test_list_transactions_filters_by_status(client):
    dealer_id, user, customer, vehicle = _setup(client)
    draft = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    response = client.get("/v1/transactions?status=draft", headers=_bearer(token))
    ids = [item["id"] for item in response.json()["items"]]
    assert draft["id"] in ids


# --- audit log ------------------------------------------------------------------


def test_audit_log_records_create_and_complete(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle, amount="1000.00")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(f"/v1/transactions/{body['id']}/complete", headers={**_bearer(token), "If-Match": "1"})

    log = client.get(f"/v1/transactions/{body['id']}/audit-log", headers=_bearer(token))
    actions = [item["action"] for item in log.json()["items"]]
    assert "create" in actions
    assert "complete" in actions


def test_audit_log_records_cancel_reason(client):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/transactions/{body['id']}/cancel",
        json={"reason": "budget fell through"},
        headers={**_bearer(token), "If-Match": "1"},
    )

    log = client.get(f"/v1/transactions/{body['id']}/audit-log", headers=_bearer(token))
    cancel_event = next(item for item in log.json()["items"] if item["action"] == "cancel")
    assert cancel_event["reason"] == "budget fell through"


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY])
def test_non_admin_auditor_roles_cannot_read_audit_log(client, role):
    dealer_id, user, customer, vehicle = _setup(client)
    body = _create_transaction(client, dealer_id, user, customer, vehicle)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/transactions/{body['id']}/audit-log", headers=_bearer(token))
    assert response.status_code == 403
