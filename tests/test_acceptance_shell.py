"""Shell acceptance tests (issue #7) — the shell's own acceptance criteria
(PLANS/DMS_MDM_V1_SPEC.md "Acceptance criteria for v1", 1-9) asserted
end-to-end against the merged system, not each entity's own unit tests in
isolation. Every per-field/per-role edge case already has dedicated
coverage in tests/test_{dealer,user,customer,vehicle,login,reference_data}.py
and the Sales tests — this file proves the *connected* scenarios spec'd as
acceptance criteria, and doesn't re-derive what's already covered there.

The shell's fourth entity, the legacy Sales `transaction`, is gone (ADR-050
retired its writes; KAN-555 dropped the table, ADR-117): the sale and
trade-in walkthroughs of AC4 and AC8's cancel live on as Sales' offer and
contract flows (tests/test_sales_lifecycle_reservation.py,
tests/test_sales_trade_in.py), and AC7 and AC9 assert on a sales offer, the
record that replaced it.
"""

import uuid

import pytest

import app.model_registry
from app.core.auth import AccessRole, create_access_token
from app.db import Base
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

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
        group_id=dealer_group_of(str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client, **overrides) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    payload.update(overrides)
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    from tests.identity_support import set_mfa_enforced

    set_mfa_enforced(client, response.json()["id"], False)  # KAN-196: the walkthrough signs in with a password alone
    return response.json()["id"]


def _grant_sales_module(client, dealer_id: str) -> None:
    response = client.put(
        f"/v1/dealerships/{dealer_id}/entitlements",
        json={"modules": ["sales"]},
        headers={**_bearer(_token(AccessRole.PLATFORM_ADMIN)), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": f"anna-{uuid.uuid4().hex[:8]}@example.ch",  # User.email is globally unique
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": True,
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _login_and_get_session_cookie(client, mailbox, user: dict) -> str:
    """Bootstraps a real session the way a real browser would — the invited
    user accepts the recorded invitation mail, sets a password and signs in
    through POST /v1/auth/login (KAN-196) — proves the login system actually
    integrates with the rest of the API's auth boundary, not a manually-
    minted bearer token.
    """

    from tests.identity_support import session_cookie_for

    return session_cookie_for(client, mailbox, user)


def _random_vin() -> str:
    import random

    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # excludes I, O, Q per ISO 3779
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, headers: dict[str, str]) -> dict:
    """A vehicle-mdm record (the legacy `POST /v1/vehicles` went with KAN-79)."""

    response = client.post("/v1/vehicle-mdm", json={"vin": _random_vin()}, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()["vehicle"]



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


def test_ac2_to_4_full_shell_walkthrough_sale(client, mailbox):
    """Bootstraps a Dealer, logs in as its admin over a real session cookie
    (not a manually-minted bearer token — proves the login system actually
    integrates with the rest of the API's auth boundary), creates a
    Customer with a contact method, and creates a Vehicle via manual VIN
    entry — the single most important connected scenario in the shell.

    AC4's own "sale Transaction, draft -> completed, Vehicle status +
    custodian update" step went with the legacy table (ADR-050, KAN-555):
    Sales' confirm_contract flow supersedes it on StockItem/VehicleMdm
    (WP-5/7); the connected sale scenario lives in
    tests/test_sales_lifecycle_reservation.py. What is left here is that the
    retired route is gone.
    """

    dealer_id = _create_dealer(client)
    # KAN-198: a freshly created Betrieb holds no module licence; license
    # sales the way platform_admin does before the user signs in, so the 404
    # below is the retired route's absence, not the missing licence.
    _grant_sales_module(client, dealer_id)
    user = _create_user(client, dealer_id, email="walkthrough@example.ch")
    session_token = _login_and_get_session_cookie(client, mailbox, user)
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

    # AC3: a vehicle by VIN entry. The legacy shell's vehicle (custodian,
    # implicit custody event) went with KAN-79; vehicle-mdm's own creation
    # and VIN rules are covered in test_vehicle_mdm_api.py.
    _create_vehicle(client, dealer_id, headers)

    # AC4 (retired path) — the legacy transaction routes are gone (KAN-555).
    assert not [path for path in client.app.openapi()["paths"] if path.startswith("/v1/transactions")]
    assert client.get("/v1/transactions", headers=headers).status_code == 404


# --- AC7: cross-tenant isolation across all four entities -----------------------


def test_ac7_cross_tenant_isolation_across_all_entities(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    user_a = _create_user(client, dealer_a)
    token_a = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a), user_id=uuid.UUID(user_a["id"])))
    token_b = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b)))

    customer_a = _create_customer(client, token_a)
    # The sales offer took the legacy transaction's place (ADR-050, KAN-555).
    offer_a = client.post("/v1/sales/offers", headers=token_a).json()

    # Dealer B cannot read Dealer A's Customer or offer at all (404).
    assert client.get(f"/v1/customers/{customer_a['id']}", headers=token_b).status_code == 404
    assert client.get(f"/v1/sales/offers/{offer_a['id']}", headers=token_b).status_code == 404
    # ...nor via list endpoints.
    assert client.get("/v1/customers", headers=token_b).json()["items"] == []
    assert client.get("/v1/sales/offers", headers=token_b).json()["items"] == []
    # ...nor mutate them.
    assert (
        client.patch(
            f"/v1/customers/{customer_a['id']}", json={"lastName": "Hijacked"}, headers={**token_b, "If-Match": "1"}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/v1/sales/offers/{offer_a['id']}", json={"vehicleLabel": "x"}, headers={**token_b, "If-Match": "1"}
        ).status_code
        == 404
    )


# --- AC5: audit_event coverage across all four entities -------------------------


def test_ac5_audit_trail_covers_all_four_entities(client):
    """AC5's fourth entity (Transaction, "complete" audited with actor
    recorded) is gone (ADR-050 retired its writes, KAN-555 dropped the
    table), and `app.sales` has not (yet) grown its own `GET .../audit-log`
    endpoint the way
    dealership/customer/vehicle each have (no PR in this package added
    one). The legacy vehicle's audit trail went with its table (KAN-79;
    vehicle-mdm's identity PATCH audit is covered in its own tests). The
    dealership and customer write-audit proof stays as-is below; a SalesOffer/SalesContract audit-log endpoint is a
    gap worth flagging for a future PR, not something to invent here just
    to keep this test's original "four" framing.
    """

    admin_token = _bearer(_token(AccessRole.PLATFORM_ADMIN))
    dealer_id = _create_dealer(client)
    dealer_token = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id)))
    _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_token)

    # Dealer: legal-identity change (KAN-197: legal name, UID, VAT are the audited
    # fields; KAN-203: saved only with the confirmation).
    client.patch(
        f"/v1/dealerships/{dealer_id}",
        json={"legalName": "Garage Umbenannt AG", "confirmation": True},
        headers={**admin_token, "If-Match": "1"},
    )
    dealer_log = client.get(f"/v1/dealerships/{dealer_id}/audit-log", headers=admin_token).json()["items"]
    assert any(
        e["action"] == "update" and e["after"].get("legal_name") == "Garage Umbenannt AG" for e in dealer_log
    )

    # Customer: PII change.
    client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neuname"},
        headers={**dealer_token, "If-Match": "1"},
    )
    customer_log = client.get(f"/v1/customers/{customer['id']}/audit-log", headers=dealer_token).json()["items"]
    assert any(e["action"] == "update" and e["after"].get("last_name") == "Neuname" for e in customer_log)

# --- AC concurrency + idempotency, asserted across representative entities -----


@pytest.mark.parametrize(
    "make_request",
    [
        lambda client, headers, ids: client.patch(
            f"/v1/customers/{ids['customer']}", json={"lastName": "X"}, headers={**headers, "If-Match": "1"}
        ),
        # The legacy Transaction's cancel used to prove this; SalesOffer's own
        # autosave PATCH, the entity that superseded it (ADR-050), does now.
        lambda client, headers, ids: client.patch(
            f"/v1/sales/offers/{ids['offer']}",
            json={"vehicleLabel": "x"},
            headers={**headers, "If-Match": "1"},
        ),
    ],
)
def test_optimistic_concurrency_409_on_stale_if_match(client, make_request):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    customer = _create_customer(client, headers)
    offer = client.post("/v1/sales/offers", headers=headers).json()
    ids = {"customer": customer["id"], "offer": offer["id"]}

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
    `uid` and `vat_number` (public business identifiers, KAN-197) are
    legitimate and don't match any of these patterns.
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
    """Asserted on a sales offer, the record that replaced the legacy
    transaction (ADR-050, KAN-555): a CHF amount keeps two decimals, a
    timestamp carries its UTC offset."""

    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    headers = _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id), user_id=uuid.UUID(user["id"])))
    offer = client.post("/v1/sales/offers", headers=headers).json()

    patched = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"manualBasePrice": "12345.67"},
        headers={**headers, "If-Match": str(offer["version"])},
    )
    assert patched.status_code == 200, patched.text
    read = client.get(f"/v1/sales/offers/{offer['id']}", headers=headers).json()

    assert read["manualBasePrice"] == "12345.67"
    # Pydantic's isoformat() serialization always includes a UTC offset for
    # timezone-aware datetimes (created_at/updated_at are DateTime(timezone=True)).
    assert read["createdAt"].endswith(("+00:00", "Z"))
