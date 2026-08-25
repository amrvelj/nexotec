"""Shell acceptance tests (issue #7) — the shell's own acceptance criteria
(PLANS/DMS_MDM_V1_SPEC.md "Acceptance criteria for v1", 1-9) asserted
end-to-end against the merged system, not each entity's own unit tests in
isolation. Every per-field/per-role edge case already has dedicated
coverage in tests/test_{dealer,user,customer,vehicle,transaction,login,
reference_data}.py — this file proves the *connected* scenarios spec'd as
acceptance criteria, and doesn't re-derive what's already covered there.

R1's two specific assertions (PM ruling, 2026-08-06 — Dealer A sees
`status: "sold"` after completing their own sale; Dealer B with no custody
history stays redacted) are NOT duplicated here — they're already covered
by tests/test_transaction.py::test_complete_sale_marks_vehicle_sold_and_
clears_custodian and ::test_dealer_with_no_custody_history_still_gets_
redacted_status_after_sale (checked per CTO's instruction before adding
anything here).
"""

import uuid

import pytest

import app.model_registry
from app.core.auth import AccessRole, create_access_token
from app.db import Base

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


def _create_dealer(client, **overrides) -> str:
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
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": f"anna-{uuid.uuid4().hex[:8]}@example.ch",  # User.email is globally unique
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": True,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _login_and_get_session_cookie(client, oidc_fake, user: dict) -> str:
    """Bootstraps a real session the way a real browser would — through the
    actual GET /v1/auth/oidc/callback route and its full user-lookup/status-
    check/token-mint logic, with only the Zitadel exchange itself faked
    (tests/fake_oidc.py) — proves the login system actually integrates with
    the rest of the API's auth boundary, not a manually-minted bearer token.
    """

    from app.platform.services.oidc import ZitadelIdentity

    oidc_fake.enqueue_identity(ZitadelIdentity(sub=user["authIdentityId"], email=user["email"], name=None))
    response = client.get("/v1/auth/oidc/callback", follow_redirects=False)
    assert response.status_code in (302, 307), response.text
    token = response.cookies.get("dms_session")
    assert token is not None
    return token


def _random_vin() -> str:
    import random

    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # excludes I, O, Q per ISO 3779
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, headers: dict[str, str], **overrides) -> dict:
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "firstName": "Peter",
        "lastName": "Beispiel",
        "language": "de",
        "emails": [
            {"emailType": "personal", "emailAddress": f"peter-{uuid.uuid4().hex[:8]}@example.ch"}
        ],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- AC1: platform admin bootstraps a Dealer + initial admin User -------------


def test_ac1_platform_admin_bootstraps_dealer_and_admin_user(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    assert user["dealershipId"] == dealer_id
    assert user["isDealerManager"] is True


# --- AC2-4: full connected walkthrough, sale ------------------------------------


def test_ac2_to_4_full_shell_walkthrough_sale(client, oidc_fake):
    """Bootstraps a Dealer, logs in as its admin over a real session cookie
    (not a manually-minted bearer token — proves the login system actually
    integrates with the rest of the API's auth boundary), creates a
    Customer with a contact method, creates a Vehicle via manual VIN entry
    (implicit custody event), and completes a `sale` Transaction — the
    single most important connected scenario in the shell.
    """

    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id, email="walkthrough@example.ch")
    session_token = _login_and_get_session_cookie(client, oidc_fake, user)
    headers = {"Cookie": f"dms_session={session_token}"}

    # AC2: Customer with at least one contact method. Post-Phase-B the
    # contact lives in the customer_email child collection, not on the
    # customer body, so assert it through the emails endpoint.
    customer = _create_customer(
        client, headers, emails=[{"emailType": "personal", "emailAddress": "customer@example.ch"}]
    )
    contact_emails = client.get(f"/v1/customers/{customer['id']}/emails", headers=headers).json()["items"]
    assert [e["emailAddress"] for e in contact_emails] == ["customer@example.ch"]
    assert contact_emails[0]["isPrimary"] is True
    # AC2 also now covers the business key being issued at creation (D-02).
    assert customer["customerNumber"].startswith("K-")

    # AC3: Vehicle via VIN entry, with an implicit custody event linking it
    # to the creating Dealer (VIN-decode itself is stubbed per the Swiss
    # addendum — no AUTO-i-DAT integration built — manual entry is the only
    # path in the shell, so make/model/year are supplied directly here
    # rather than "populated by decode").
    vehicle = _create_vehicle(client, dealer_id, headers)
    assert vehicle["currentCustodianPartnerId"] == dealer_id
    custody = client.get(f"/v1/vehicles/{vehicle['id']}/custody-events", headers=headers)
    assert custody.status_code == 200
    assert len(custody.json()["items"]) == 1
    assert custody.json()["items"][0]["eventType"] == "acquired"

    # AC4: sale Transaction, draft -> completed, Vehicle status + custodian
    # update accordingly.
    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "primaryUserId": user["id"],
            "amount": "42000.00",
        },
        headers=headers,
    )
    assert transaction.status_code == 201, transaction.text
    body = transaction.json()
    assert body["status"] == "draft"

    completed = client.post(
        f"/v1/transactions/{body['id']}/complete", headers={**headers, "If-Match": "1"}
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["transactionDate"] is not None

    vehicle_after = client.get(f"/v1/vehicles/{vehicle['id']}", headers=headers)
    assert vehicle_after.json()["status"] == "sold"
    assert vehicle_after.json()["currentCustodianPartnerId"] is None


def test_ac4_full_shell_walkthrough_trade_in(client):
    """Same connected scenario, `trade_in` type — a different dealer
    acquires a vehicle it didn't previously custody, ending in `in_stock`
    with itself as custodian.
    """

    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    customer = _create_customer(client, headers)

    other_dealer_id = _create_dealer(client)
    trade_in_vehicle = _create_vehicle(
        client, other_dealer_id, _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(other_dealer_id)))
    )

    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "trade_in",
            "customerId": customer["id"],
            "vehicleId": trade_in_vehicle["id"],
            "primaryUserId": user["id"],
            "amount": "8000.00",
        },
        headers=headers,
    )
    assert transaction.status_code == 201, transaction.text

    completed = client.post(
        f"/v1/transactions/{transaction.json()['id']}/complete", headers={**headers, "If-Match": "1"}
    )
    assert completed.status_code == 200, completed.text

    vehicle_after = client.get(f"/v1/vehicles/{trade_in_vehicle['id']}", headers=headers)
    assert vehicle_after.json()["status"] == "in_stock"
    assert vehicle_after.json()["currentCustodianPartnerId"] == dealer_id


# --- AC8: cancel never mutates Vehicle -----------------------------------------


def test_ac8_cancel_never_mutates_vehicle(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    customer = _create_customer(client, headers)
    vehicle = _create_vehicle(client, dealer_id, headers)

    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "primaryUserId": user["id"],
        },
        headers=headers,
    ).json()

    cancelled = client.post(
        f"/v1/transactions/{transaction['id']}/cancel",
        json={"reason": "customer changed their mind"},
        headers={**headers, "If-Match": "1"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    vehicle_after = client.get(f"/v1/vehicles/{vehicle['id']}", headers=headers)
    assert vehicle_after.json()["status"] == "in_transit"  # unchanged from creation
    assert vehicle_after.json()["currentCustodianPartnerId"] == dealer_id  # unchanged


# --- AC3 (malformed/duplicate VIN) ----------------------------------------------


@pytest.mark.parametrize("bad_vin", ["1hgcm82633a004352", "1HGCM8263 A004352", "1HGCM82633A00435"])
def test_ac3_malformed_vin_rejected_not_normalized(client, bad_vin):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/vehicles",
        json={"vin": bad_vin, "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"},
        headers=_bearer(token),
    )
    assert response.status_code == 422


def test_ac3_duplicate_vin_across_tenants_is_rejected(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    token_a = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a)))
    vehicle = _create_vehicle(client, dealer_a, token_a)

    token_b = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b)))
    response = client.post(
        "/v1/vehicles",
        json={
            "vin": vehicle["vin"],
            "make": "Toyota",
            "model": "Corolla",
            "modelYear": 2021,
            "condition": "new",
        },
        headers=token_b,
    )
    assert response.status_code == 409


# --- AC7: cross-tenant isolation across all four entities -----------------------


def test_ac7_cross_tenant_isolation_across_all_entities(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    token_a = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a)))
    token_b = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b)))
    user_a = _create_user(client, dealer_a)

    customer_a = _create_customer(client, token_a)
    vehicle_a = _create_vehicle(client, dealer_a, token_a)
    transaction_a = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer_a["id"],
            "vehicleId": vehicle_a["id"],
            "primaryUserId": user_a["id"],
        },
        headers=token_a,
    ).json()

    # Dealer B cannot read Dealer A's Customer or Transaction at all (404).
    assert client.get(f"/v1/customers/{customer_a['id']}", headers=token_b).status_code == 404
    assert client.get(f"/v1/transactions/{transaction_a['id']}", headers=token_b).status_code == 404
    # ...nor via list endpoints.
    assert client.get("/v1/customers", headers=token_b).json()["items"] == []
    assert client.get("/v1/transactions", headers=token_b).json()["items"] == []
    # ...nor mutate them.
    assert (
        client.patch(
            f"/v1/customers/{customer_a['id']}", json={"lastName": "Hijacked"}, headers={**token_b, "If-Match": "1"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/transactions/{transaction_a['id']}/complete", headers={**token_b, "If-Match": "1"}
        ).status_code
        == 404
    )

    # Vehicle is tenant-agnostic by design (spec cross-cutting #9): Dealer B
    # *can* see the static profile (VIN/specs), required for duplicate-VIN
    # prevention and trade-in workflows, but status/custodian are redacted.
    vehicle_seen_by_b = client.get(f"/v1/vehicles/{vehicle_a['id']}", headers=token_b)
    assert vehicle_seen_by_b.status_code == 200
    assert vehicle_seen_by_b.json()["vin"] == vehicle_a["vin"]
    assert vehicle_seen_by_b.json()["status"] is None
    assert vehicle_seen_by_b.json()["currentCustodianPartnerId"] is None
    # ...and B's own custody-events view of A's vehicle is empty (row-filtered).
    assert (
        client.get(f"/v1/vehicles/{vehicle_a['id']}/custody-events", headers=token_b).json()["items"] == []
    )


# --- AC5: audit_event coverage across all four entities -------------------------


def test_ac5_audit_trail_covers_all_four_entities(client):
    admin_token = _bearer(_token(AccessRole.PLATFORM_ADMIN))
    dealer_id = _create_dealer(client)
    dealer_token = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id)))
    user = _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_token)
    vehicle = _create_vehicle(client, dealer_id, dealer_token)
    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "primaryUserId": user["id"],
            "amount": "1000.00",
        },
        headers=dealer_token,
    ).json()

    # Dealer: license field change.
    client.patch(
        f"/v1/dealerships/{dealer_id}",
        json={"dealerLicenseNumber": "ZH-99999"},
        headers={**admin_token, "If-Match": "1"},
    )
    dealer_log = client.get(f"/v1/dealerships/{dealer_id}/audit-log", headers=admin_token).json()["items"]
    assert any(
        e["action"] == "update" and e["after"].get("dealer_license_number") == "ZH-99999" for e in dealer_log
    )

    # Customer: PII change.
    client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neuname"},
        headers={**dealer_token, "If-Match": "1"},
    )
    customer_log = client.get(f"/v1/customers/{customer['id']}/audit-log", headers=dealer_token).json()["items"]
    assert any(e["action"] == "update" and e["after"].get("last_name") == "Neuname" for e in customer_log)

    # Vehicle: title/custody-adjacent field change (odometer) + the
    # implicit "acquired" custody event from creation both land in the
    # audit trail.
    client.patch(
        f"/v1/vehicles/{vehicle['id']}", json={"odometer": 15000}, headers={**dealer_token, "If-Match": "1"}
    )
    vehicle_log = client.get(f"/v1/vehicles/{vehicle['id']}/audit-log", headers=dealer_token).json()["items"]
    assert any(e["action"] == "create" for e in vehicle_log)
    assert any(e["action"] == "update" and e["after"].get("odometer") == 15000 for e in vehicle_log)

    # Transaction: status change (complete), with actor recorded.
    client.post(f"/v1/transactions/{transaction['id']}/complete", headers={**dealer_token, "If-Match": "1"})
    transaction_log = client.get(
        f"/v1/transactions/{transaction['id']}/audit-log", headers=dealer_token
    ).json()["items"]
    complete_event = next(e for e in transaction_log if e["action"] == "complete")
    assert complete_event["after"]["status"] == "completed"
    assert complete_event["actorId"] is not None


# --- AC concurrency + idempotency, asserted across representative entities -----


@pytest.mark.parametrize(
    "make_request",
    [
        lambda client, headers, ids: client.patch(
            f"/v1/customers/{ids['customer']}", json={"lastName": "X"}, headers={**headers, "If-Match": "1"}
        ),
        lambda client, headers, ids: client.patch(
            f"/v1/vehicles/{ids['vehicle']}", json={"odometer": 1}, headers={**headers, "If-Match": "1"}
        ),
        lambda client, headers, ids: client.post(
            f"/v1/transactions/{ids['transaction']}/cancel", json={}, headers={**headers, "If-Match": "1"}
        ),
    ],
)
def test_optimistic_concurrency_409_on_stale_if_match(client, make_request):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    customer = _create_customer(client, headers)
    vehicle = _create_vehicle(client, dealer_id, headers)
    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "primaryUserId": user["id"],
        },
        headers=headers,
    ).json()
    ids = {"customer": customer["id"], "vehicle": vehicle["id"], "transaction": transaction["id"]}

    first = make_request(client, headers, ids)
    assert first.status_code == 200, first.text

    stale = make_request(client, headers, ids)  # same stale If-Match: "1" again
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"


def test_idempotency_key_replay_returns_original_result_not_a_duplicate(client):
    dealer_id = _create_dealer(client)
    token = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id)))
    headers = {**token, "Idempotency-Key": "acceptance-test-key-1"}

    payload = {
        "firstName": "Peter",
        "lastName": "Beispiel",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": "idempotent@example.ch"}],
    }
    first = client.post("/v1/customers", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    replay = client.post("/v1/customers", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]  # same record, not a new one

    all_customers = client.get("/v1/customers", headers=token).json()["items"]
    assert len(all_customers) == 1  # no duplicate was created


# --- AC6: schema review — no payment/SSN/DL data anywhere -----------------------

_FORBIDDEN_COLUMN_SUBSTRINGS = (
    "ssn",
    "social_security",
    "credit_card",
    "card_number",
    "cvv",
    "cvc",
    "driver_license",
    "drivers_license",
    "dl_number",
)


def test_ac6_schema_has_no_payment_card_ssn_or_drivers_license_columns():
    """Automated schema review, not just code review — introspects every
    mapped table's column names directly from SQLAlchemy metadata.
    `dealer_license_number` (a business operating license, not a personal
    driver's license) and `tax_id` (encrypted at rest, a business VAT-style
    identifier, not a personal SSN) are legitimate and don't match any of
    these patterns.
    """

    assert app.model_registry  # ensures all model modules are imported, metadata populated
    all_columns = [
        column.name.lower() for table in Base.metadata.tables.values() for column in table.columns
    ]
    assert all_columns, "expected at least one mapped table"

    violations = [
        column_name
        for column_name in all_columns
        for forbidden in _FORBIDDEN_COLUMN_SUBSTRINGS
        if forbidden in column_name
    ]
    assert violations == []


# --- AC9: UTC ISO-8601 timestamps, 2-decimal money (CHF per Swiss addendum) -----


def test_ac9_timestamps_are_utc_iso8601_and_amount_is_two_decimal_precision(client):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    customer = _create_customer(client, headers)
    vehicle = _create_vehicle(client, dealer_id, headers)

    transaction = client.post(
        "/v1/transactions",
        json={
            "transactionType": "sale",
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "primaryUserId": user["id"],
            "amount": "12345.67",
        },
        headers=headers,
    ).json()

    assert transaction["amount"] == "12345.67"
    # Pydantic's isoformat() serialization always includes a UTC offset for
    # timezone-aware datetimes (created_at/updated_at are DateTime(timezone=True)).
    created_at = transaction["createdAt"]
    assert created_at.endswith(("+00:00", "Z"))
