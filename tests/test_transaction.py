"""WP-8 PR-7 (ADR-050/S-D12): `transaction` is RETIRED — every mutating
operation refuses with 409, reads keep working. This file used to
exercise the full legacy create/update/complete/cancel lifecycle (see git
history for that shape, superseded by app.sales.services.contract's own
confirm/cancel path, tests/test_sales_lifecycle_reservation.py); it now
tests exactly the retirement contract instead: writes refused, reads
intact, deprecated in OpenAPI.
"""

import random
import uuid

from sqlalchemy.orm import sessionmaker

from app.core.auth import AccessRole, create_access_token
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=_tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
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
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Sam",
        "lastName": "Sales",
        "email": f"sam-{uuid.uuid4().hex[:8]}@example.ch",
        "role": "sales",
        "accessRoles": ["sales"],
        "isDealerManager": False,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Peter",
        "lastName": "Beispiel",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": "peter@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _random_vin() -> str:
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # excludes I, O, Q per ISO 3779
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _setup(client, dealer_id: str | None = None):
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


def _seed_transaction_directly(engine, *, dealer_id: str, user: dict, customer: dict, vehicle: dict) -> Transaction:
    """The service layer refuses this now (that's the whole point of this
    file) — so a READ-path test that needs an existing row seeds it
    directly via the ORM instead, exactly like seeding any other retired-
    but-still-real table. A genuinely separate session bound to the same
    (StaticPool, single-connection) test engine the `client` fixture uses.
    """

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        transaction = Transaction(
            tenant_id=uuid.UUID(dealer_id),
            transaction_type=TransactionType.SALE,
            status=TransactionStatus.DRAFT,
            customer_id=uuid.UUID(customer["id"]),
            vehicle_id=uuid.UUID(vehicle["id"]),
            primary_user_id=uuid.UUID(user["id"]),
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)
        return transaction
    finally:
        session.close()


# --- every mutating operation refuses (WP-8 PR-7) --------------------------------


def test_create_is_refused(client):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 409, response.text
    assert "sales_offer" in response.json()["error"]["message"] or "sales_contract" in response.json()["error"]["message"]


def test_create_refused_regardless_of_write_role(client):
    """The capability check (Depends(require_write("transactions"))) still
    runs first and still passes for SALES — the retirement refusal is a
    SEPARATE, additional gate inside the service layer, not a permission
    change."""

    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 409, response.text


def test_non_write_roles_still_get_403_before_reaching_the_retirement_check(client):
    dealer_id, user, customer, vehicle = _setup(client)
    token = _token(AccessRole.INVENTORY, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/transactions", json=_transaction_payload(user, customer, vehicle), headers=_bearer(token)
    )
    assert response.status_code == 403, response.text


def test_patch_is_refused(client, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/transactions/{transaction.id}",
        json={"notes": "test"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 409, response.text


def test_complete_is_refused(client, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/transactions/{transaction.id}/complete", headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 409, response.text


def test_cancel_is_refused(client, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/transactions/{transaction.id}/cancel",
        json={"reason": "irrelevant now"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 409, response.text


# --- reads keep working ("never dropped") ----------------------------------------


def test_get_transaction_still_works(client, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/transactions/{transaction.id}", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(transaction.id)


def test_get_transaction_cross_tenant_is_404(client, engine):
    dealer_id, user, customer, vehicle = _setup(client)
    transaction = _seed_transaction_directly(engine, dealer_id=dealer_id, user=user, customer=customer, vehicle=vehicle)
    other_token = _token(is_dealer_manager=True)  # different, random tenant
    response = client.get(f"/v1/transactions/{transaction.id}", headers=_bearer(other_token))
    assert response.status_code == 404


def test_list_transactions_still_works_and_is_tenant_scoped(client, engine):
    dealer_a, user_a, customer_a, vehicle_a = _setup(client)
    dealer_b = _create_dealer(client)
    _seed_transaction_directly(engine, dealer_id=dealer_a, user=user_a, customer=customer_a, vehicle=vehicle_a)

    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a))
    response_a = client.get("/v1/transactions", headers=_bearer(token_a))
    assert len(response_a.json()["items"]) == 1

    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b))
    response_b = client.get("/v1/transactions", headers=_bearer(token_b))
    assert response_b.json()["items"] == []


# --- deprecation is visible in the OpenAPI schema ---------------------------------


def test_every_transactions_operation_is_marked_deprecated(client):
    schema = client.app.openapi()
    transaction_paths = {path: ops for path, ops in schema["paths"].items() if "/transactions" in path}
    assert transaction_paths, "no /transactions paths found in the OpenAPI schema"
    for path, operations in transaction_paths.items():
        for method, op in operations.items():
            if method not in ("get", "post", "patch", "put", "delete"):
                continue
            assert op.get("deprecated") is True, f"{method.upper()} {path} is not marked deprecated"
