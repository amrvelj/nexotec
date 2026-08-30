"""Merge re-pointing (D-08): vehicle-party rows, transactions, phones,
emails and external IDs must follow the duplicate into the survivor, per
FR-09 and the PRD's own Risks section ("merge without re-pointing is worse
than no merge — treat as a correctness bug"). See test_customer.py for the
basic flag-flip merge tests; this file is scoped to the re-pointing behavior
specifically.
"""

import uuid

from sqlalchemy.orm import sessionmaker

from app.core.auth import AccessRole, create_access_token
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
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
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _random_vin() -> str:
    import random

    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_transaction(engine, dealer_id: str, user: dict, customer: dict, vehicle: dict) -> dict:
    """WP-8 PR-7 (ADR-050/S-D12): `transaction` writes are retired at the
    service layer — this seeds directly via the ORM instead, exactly like
    seeding any other retired-but-still-real table, on a session bound to
    the same test engine `client` itself uses.
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
        return {"id": str(transaction.id)}
    finally:
        session.close()


def _merge(client, dealer_id: str, duplicate_id: str, survivor_id: str):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{duplicate_id}/merge",
        json={"duplicateOfCustomerId": survivor_id},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _add_phone(client, dealer_id: str, customer_id: str, phone_e164: str, **overrides):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"phoneType": "mobile", "phoneE164": phone_e164}
    payload.update(overrides)
    response = client.post(f"/v1/customers/{customer_id}/phones", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _add_email(client, dealer_id: str, customer_id: str, email_address: str, **overrides):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"emailType": "personal", "emailAddress": email_address}
    payload.update(overrides)
    response = client.post(f"/v1/customers/{customer_id}/emails", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _add_external_id(client, dealer_id: str, customer_id: str, system_name: str, external_id: str):
    token = _token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"systemName": system_name, "externalId": external_id}
    response = client.post(f"/v1/customers/{customer_id}/external-ids", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- vehicle parties -----------------------------------------------------------


def test_merge_repoints_vehicle_party_to_survivor(client, db_session):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)

    party = VehicleParty(
        vehicle_id=uuid.UUID(vehicle["id"]), customer_id=uuid.UUID(duplicate["id"]), role=VehiclePartyRole.OWNER
    )
    db_session.add(party)
    db_session.commit()

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    # The merge happened on a different SQLAlchemy session (TestClient's
    # dependency-injected one) — db_session's identity map still holds the
    # pre-merge `party` object (expire_on_commit=False), so force a refetch.
    db_session.expire_all()
    remaining = db_session.query(VehicleParty).filter(VehicleParty.vehicle_id == uuid.UUID(vehicle["id"])).all()
    assert len(remaining) == 1
    assert remaining[0].customer_id == uuid.UUID(survivor["id"])


def test_merge_drops_vehicle_party_already_on_survivor(client, db_session):
    """Same vehicle/role/effective_from already exists on the survivor —
    re-pointing would violate uq_vehicle_party_scope, so the duplicate's
    copy is dropped instead of erroring.
    """

    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)

    shared_effective_from = __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)
    db_session.add(
        VehicleParty(
            vehicle_id=uuid.UUID(vehicle["id"]),
            customer_id=uuid.UUID(survivor["id"]),
            role=VehiclePartyRole.OWNER,
            effective_from=shared_effective_from,
        )
    )
    db_session.add(
        VehicleParty(
            vehicle_id=uuid.UUID(vehicle["id"]),
            customer_id=uuid.UUID(duplicate["id"]),
            role=VehiclePartyRole.OWNER,
            effective_from=shared_effective_from,
        )
    )
    db_session.commit()

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    db_session.expire_all()
    remaining = db_session.query(VehicleParty).filter(VehicleParty.vehicle_id == uuid.UUID(vehicle["id"])).all()
    assert len(remaining) == 1
    assert remaining[0].customer_id == uuid.UUID(survivor["id"])


# --- transactions ----------------------------------------------------------------


def test_merge_repoints_transactions(client, engine):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(engine, dealer_id, user, duplicate, vehicle)

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/transactions/{txn['id']}", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["customerId"] == survivor["id"]


# --- sales_offer / sales_contract (WP-8 PR-7) -------------------------------------


def test_merge_repoints_sales_offers(client):
    """A merge must repoint the NEW tables too, not just the retired
    `transaction` table repoint_customer_transactions already handled
    (app.sales.public.repoint_customer_sales_records, wired into
    merge_customer)."""

    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"customerId": duplicate["id"]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    refetched = client.get(f"/v1/sales/offers/{offer['id']}", headers=_bearer(token))
    assert refetched.json()["customerId"] == survivor["id"]


# --- phones / emails ---------------------------------------------------------------


def test_merge_repoints_unique_phone_and_email(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_phone(client, dealer_id, duplicate["id"], "+41791112233")
    _add_email(client, dealer_id, duplicate["id"], "dup-only@example.ch")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phones = client.get(f"/v1/customers/{survivor['id']}/phones", headers=_bearer(token)).json()["items"]
    emails = client.get(f"/v1/customers/{survivor['id']}/emails", headers=_bearer(token)).json()["items"]
    assert "+41791112233" in {p["phoneE164"] for p in phones}
    assert "dup-only@example.ch" in {e["emailAddress"] for e in emails}
    # Exactly one primary each, even though the survivor gained rows.
    assert sum(1 for p in phones if p["isPrimary"]) == 1
    assert sum(1 for e in emails if e["isPrimary"]) == 1


def test_merge_dedupes_conflicting_phone_and_email(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, emails=[{"emailType": "personal", "emailAddress": "shared@example.ch"}])
    duplicate = _create_customer(client, dealer_id, emails=[{"emailType": "personal", "emailAddress": "shared@example.ch"}])
    _add_phone(client, dealer_id, survivor["id"], "+41799998877")
    _add_phone(client, dealer_id, duplicate["id"], "+41799998877")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phones = client.get(f"/v1/customers/{survivor['id']}/phones", headers=_bearer(token)).json()["items"]
    emails = client.get(f"/v1/customers/{survivor['id']}/emails", headers=_bearer(token)).json()["items"]
    assert [p["phoneE164"] for p in phones].count("+41799998877") == 1
    assert [e["emailAddress"] for e in emails].count("shared@example.ch") == 1
    assert sum(1 for p in phones if p["isPrimary"]) == 1
    assert sum(1 for e in emails if e["isPrimary"]) == 1


# --- external ids -------------------------------------------------------------------


def test_merge_repoints_external_id(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_external_id(client, dealer_id, duplicate["id"], "crm", "CRM-123")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    rows = client.get(f"/v1/customers/{survivor['id']}/external-ids", headers=_bearer(token)).json()["items"]
    assert {(r["systemName"], r["externalId"]) for r in rows} == {("crm", "CRM-123")}


def test_merge_drops_external_id_when_survivor_already_has_that_system(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_external_id(client, dealer_id, survivor["id"], "crm", "SURVIVOR-1")
    _add_external_id(client, dealer_id, duplicate["id"], "crm", "DUPLICATE-1")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    rows = client.get(f"/v1/customers/{survivor['id']}/external-ids", headers=_bearer(token)).json()["items"]
    # Survivor's own linkage wins; the duplicate's is dropped, not merged in.
    assert {(r["systemName"], r["externalId"]) for r in rows} == {("crm", "SURVIVOR-1")}


# --- audit -----------------------------------------------------------------------------


def test_merge_audit_log_reports_repoint_counts(client, engine):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    _create_transaction(engine, dealer_id, user, duplicate, vehicle)
    _add_phone(client, dealer_id, duplicate["id"], "+41791234599")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    log = client.get(f"/v1/customers/{duplicate['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200, log.text
    merge_event = next(item for item in log.json()["items"] if item["action"] == "merge")
    assert merge_event["after"]["transactionsRepointed"] == 1
    assert merge_event["after"]["phonesRepointed"] == 1
