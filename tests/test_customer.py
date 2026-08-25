import uuid

import pytest
from sqlalchemy import text

from app.core.auth import AccessRole, create_access_token

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}

# Customer's own nested address shape (WP-3 PR-5, ADR-067) — field names
# match CustomerAddress's own attribute names, not Dealership's VALID_ADDRESS
# shape above.
VALID_CUSTOMER_ADDRESS = {
    "addressType": "domicile",
    "addressStreet": "Bahnhofstrasse",
    "addressHouseNumber": "1",
    "addressPostalCode": "8001",
    "addressLocality": "Zürich",
    "addressCountry": "CH",
}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
    group_id: uuid.UUID | None = None,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=_tid,
        # Derived deterministically from tenant_id when not given explicitly
        # — every token minted for the "same dealer" (same tenant_id) within
        # a test then shares the same group_id without a real DB round trip.
        # Pass group_id explicitly to test true group-wide behaviour across
        # two different tenant_ids (see test_group_wide_* below).
        group_id=group_id or uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
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


def _create_dealer_full(client, **overrides) -> dict:
    """Like _create_dealer, but returns the full body — needed when a test
    wants the real dealerGroupId (e.g. to add a second dealership to it).
    """

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
    return response.json()


def _customer_payload(**overrides):
    """Phase B contract: `language` is mandatory, and contact details are
    nested rather than flat `email`/`phone` fields (PRD D-01, D-03, D-04).
    """

    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": "anna@example.ch"}],
    }
    payload.update(overrides)
    return payload


def _create_customer(client, dealer_id: str, **overrides):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/customers", json=_customer_payload(**overrides), headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- creation / access control ----------------------------------------------


def test_dealer_admin_can_create_customer(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(client, dealer_id)
    assert body["lifecycleStatus"] == "prospect"
    assert body["customerType"] == "individual"
    assert body["version"] == 1


def test_sales_can_create_customer(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", [AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_write_roles_cannot_create_customer(client, role):
    dealer_id = _create_dealer(client)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token))
    assert response.status_code == 403


def test_create_customer_requires_authentication(client):
    response = client.post("/v1/customers", json=_customer_payload())
    assert response.status_code == 401


def test_create_customer_under_nonexistent_tenant_is_404(client):
    token = _token(is_dealer_manager=True)  # random tenant_id, no real Dealer row
    response = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token))
    assert response.status_code == 404


# --- field validation ---------------------------------------------------------


def test_a_contact_point_is_required(client):
    """FR-03: a customer must be reachable. Post-Phase-B this is checked
    against the nested phones/emails, not the removed flat columns.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(emails=[])
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_phone_only_customer_is_allowed(client):
    """The walk-in case (US-03): capture a phone number now, finish later."""

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(emails=[], phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}])
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text


def test_language_is_required(client):
    """D-01: correspondence language is never inferred server-side."""

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload()
    del payload["language"]
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_phone_only_is_sufficient(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(email=None, phone="+41791234567")
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text


def test_invalid_email_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers",
        json=_customer_payload(emails=[{"emailType": "personal", "emailAddress": "not-an-email"}]),
        headers=_bearer(token),
    )
    assert response.status_code == 422


def test_invalid_phone_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers",
        json=_customer_payload(phones=[{"phoneType": "mobile", "phoneE164": "0791234567"}]),
        headers=_bearer(token),
    )
    assert response.status_code == 422


def test_cannot_create_customer_already_merged(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(lifecycleStatus="merged"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_two_customers_may_share_an_email(client):
    """Inverted by D-05. The tenant-wide unique constraint on email used to
    return 409 here, which blocked two family members or two employees of the
    same company — an everyday case at a dealership. A shared address is now
    a duplicate-detection *signal* (FR-04), not a hard error.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    shared = [{"emailType": "personal", "emailAddress": "haushalt@example.ch"}]
    _create_customer(client, dealer_id, firstName="Anna", emails=shared)
    response = client.post(
        "/v1/customers",
        json=_customer_payload(firstName="Beat", emails=shared),
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text

    candidates = client.get(
        "/v1/customers/duplicate-check?q=haushalt@example.ch", headers=_bearer(token)
    ).json()["items"]
    assert len(candidates) == 2
    assert all(c["match"] == "exact" for c in candidates)


def test_duplicate_email_across_tenants_is_allowed(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_customer(client, dealer_a, email="shared@example.ch")
    response = client.post(
        "/v1/customers",
        json=_customer_payload(email="shared@example.ch"),
        headers=_bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b))),
    )
    assert response.status_code == 201


def test_address_round_trips(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(client, dealer_id, addresses=[VALID_CUSTOMER_ADDRESS])
    assert body["address"]["addressLocality"] == "Zürich"
    assert body["address"]["addressCountry"] == "CH"


def test_no_address_at_creation_returns_null(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(client, dealer_id)
    assert body["address"] is None


# --- cross-tenant isolation ---------------------------------------------------


def test_get_customer_cross_tenant_is_404(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)

    other_token = _token(is_dealer_manager=True)  # different, random tenant_id
    response = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(other_token))
    assert response.status_code == 404


def test_get_unknown_customer_is_404(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{uuid.uuid4()}", headers=_bearer(token))
    assert response.status_code == 404


# --- optimistic concurrency ---------------------------------------------------


def test_patch_without_if_match_is_400(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}", json={"lastName": "Muster-Meier"}, headers=_bearer(token)
    )
    assert response.status_code == 400


def test_patch_with_stale_if_match_is_409(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    headers = {**_bearer(token), "If-Match": "1"}

    first = client.patch(f"/v1/customers/{customer['id']}", json={"lastName": "A"}, headers=headers)
    assert first.status_code == 200

    stale = client.patch(f"/v1/customers/{customer['id']}", json={"lastName": "B"}, headers=headers)
    assert stale.status_code == 409


def test_patch_updates_customer_and_bumps_version(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neuname"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["lastName"] == "Neuname"
    assert response.json()["version"] == 2


def test_deleting_the_last_contact_point_is_rejected(client):
    """The FR-03 invariant has to hold for the life of the record, not only
    at creation — otherwise it decays into customers nobody can reach.
    """

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_email = client.get(f"/v1/customers/{customer['id']}/emails", headers=_bearer(token)).json()["items"][0]
    response = client.delete(
        f"/v1/customers/{customer['id']}/emails/{only_email['id']}", headers=_bearer(token)
    )
    assert response.status_code == 400


def test_deleting_an_email_is_allowed_when_a_phone_remains(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client, dealer_id, phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}]
    )
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_email = client.get(f"/v1/customers/{customer['id']}/emails", headers=_bearer(token)).json()["items"][0]
    response = client.delete(
        f"/v1/customers/{customer['id']}/emails/{only_email['id']}", headers=_bearer(token)
    )
    assert response.status_code == 204


def test_patch_cannot_set_lifecycle_status_merged_directly(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lifecycleStatus": "merged"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", [AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_write_roles_cannot_patch_customer(client, role):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}", json={"lastName": "X"}, headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 403


# --- merge ---------------------------------------------------------------------


def test_merge_sets_lifecycle_status_and_duplicate_of(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, email="survivor@example.ch")
    duplicate = _create_customer(client, dealer_id, email="dup1@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    response = client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycleStatus"] == "merged"
    assert body["duplicateOfCustomerId"] == survivor["id"]
    assert body["version"] == 2


def test_merge_into_self_is_rejected(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/merge",
        json={"duplicateOfCustomerId": customer["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 400


def test_merge_into_already_merged_target_is_rejected(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, email="survivor2@example.ch")
    dup_a = _create_customer(client, dealer_id, email="dupa@example.ch")
    dup_b = _create_customer(client, dealer_id, email="dupb@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/customers/{dup_a['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    response = client.post(
        f"/v1/customers/{dup_b['id']}/merge",
        json={"duplicateOfCustomerId": dup_a["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 409


def test_merged_customer_cannot_be_patched_further(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, email="survivor3@example.ch")
    duplicate = _create_customer(client, dealer_id, email="dup3@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    response = client.patch(
        f"/v1/customers/{duplicate['id']}",
        json={"lastName": "X"},
        headers={**_bearer(token), "If-Match": "2"},
    )
    assert response.status_code == 409


# --- duplicate-check typeahead -------------------------------------------------


def test_duplicate_check_ranks_exact_match_first(client):
    dealer_id = _create_dealer(client)
    _create_customer(
        client, dealer_id, firstName="Anna", lastName="Muster",
        emails=[{"emailType": "personal", "emailAddress": "anna.muster@example.ch"}],
    )
    _create_customer(
        client, dealer_id, firstName="Annalise", lastName="Weber",
        emails=[{"emailType": "personal", "emailAddress": "annalise@example.ch"}],
    )
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))

    response = client.get(
        "/v1/customers/duplicate-check?q=anna.muster@example.ch", headers=_bearer(token)
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["primaryEmail"] == "anna.muster@example.ch"
    assert items[0]["match"] == "exact"


def test_duplicate_check_is_tenant_scoped(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_customer(client, dealer_a, firstName="Peter", lastName="Muster", email="peter@example.ch")

    token_b = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_b))
    response = client.get("/v1/customers/duplicate-check?q=Peter", headers=_bearer(token_b))
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_duplicate_check_does_not_block_create(client):
    """Advisory only — spec explicitly says not a blocking gate."""

    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, email="existing@example.ch", firstName="Anna", lastName="Muster")
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))

    check = client.get("/v1/customers/duplicate-check?q=Anna", headers=_bearer(token))
    assert len(check.json()["items"]) == 1

    create = client.post(
        "/v1/customers",
        json=_customer_payload(email="anna2@example.ch", firstName="Anna", lastName="Muster"),
        headers=_bearer(token),
    )
    assert create.status_code == 201


# --- list / filtering / pagination ---------------------------------------------


def test_list_customers_filters_by_lifecycle_status(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    _create_customer(client, dealer_id, email="a@example.ch")
    prospect = _create_customer(client, dealer_id, email="b@example.ch")

    client.patch(
        f"/v1/customers/{prospect['id']}",
        json={"lifecycleStatus": "active"},
        headers={**_bearer(token), "If-Match": "1"},
    )

    response = client.get("/v1/customers?lifecycle_status=active", headers=_bearer(token))
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == prospect["id"]


def test_list_customers_filters_by_query_text(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    _create_customer(client, dealer_id, firstName="Anna", lastName="Muster", email="a@example.ch")
    _create_customer(client, dealer_id, firstName="Bruno", lastName="Keller", email="b@example.ch")

    response = client.get("/v1/customers?q=Keller", headers=_bearer(token))
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["lastName"] == "Keller"


def test_list_customers_is_tenant_scoped(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_customer(client, dealer_a, email="a@example.ch")

    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b))
    response = client.get("/v1/customers", headers=_bearer(token_b))
    assert response.json()["items"] == []


def test_list_customers_paginates(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    for i in range(3):
        _create_customer(client, dealer_id, email=f"c{i}@example.ch")

    first_page = client.get("/v1/customers?limit=2", headers=_bearer(token))
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["nextCursor"] is not None

    second_page = client.get(f"/v1/customers?limit=2&cursor={first_body['nextCursor']}", headers=_bearer(token))
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["nextCursor"] is None


# --- audit logging ---------------------------------------------------------------


def test_pii_changes_are_audit_logged(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neuname"},
        headers={**_bearer(token), "If-Match": "1"},
    )

    log = client.get(f"/v1/customers/{customer['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200
    actions = [item["action"] for item in log.json()["items"]]
    assert "create" in actions
    assert "update" in actions
    update_event = next(item for item in log.json()["items"] if item["action"] == "update")
    assert update_event["after"]["last_name"] == "Neuname"


def test_merge_logs_both_source_ids(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, email="survivor4@example.ch")
    duplicate = _create_customer(client, dealer_id, email="dup4@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )

    log = client.get(f"/v1/customers/{duplicate['id']}/audit-log", headers=_bearer(token))
    merge_event = next(item for item in log.json()["items"] if item["action"] == "merge")
    assert merge_event["entityId"] == duplicate["id"]
    assert merge_event["after"]["duplicateOfCustomerId"] == survivor["id"]


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY])
def test_non_admin_auditor_roles_cannot_read_audit_log(client, role):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{customer['id']}/audit-log", headers=_bearer(token))
    assert response.status_code == 403


# --- Customer PRD Phase A: business/individual conditional fields -----------


def test_create_business_customer(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Muster Garage AG",
        "legalForm": "ag",
        "taxId": "CHE-987.654.326",
        "language": "de",
        "emails": [{"emailType": "work", "emailAddress": "info@muster-garage.ch"}],
    }
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customerType"] == "business"
    assert body["companyName"] == "Muster Garage AG"
    assert body["legalForm"] == "ag"
    assert body["firstName"] is None
    assert body["lastName"] is None
    assert "taxId" not in body  # write-only, same as Dealer.taxId


def test_business_customer_rejects_individual_fields(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Muster Garage AG",
        "firstName": "Anna",
        "email": "info@muster-garage.ch",
    }
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_business_customer_requires_company_name(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"customerType": "business", "email": "info@muster-garage.ch"}
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_individual_customer_rejects_business_fields(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(companyName="Oops AG"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_patch_cannot_set_business_field_on_individual_customer(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="patch-mix@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"companyName": "Oops AG"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 400


def test_tax_id_is_redacted_in_audit_log(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Redact AG",
        "taxId": "CHE-111.222.338",
        "language": "de",
        "emails": [{"emailType": "work", "emailAddress": "redact@example.ch"}],
    }
    created = client.post("/v1/customers", json=payload, headers=_bearer(token)).json()
    log = client.get(f"/v1/customers/{created['id']}/audit-log", headers=_bearer(token))
    create_event = next(item for item in log.json()["items"] if item["action"] == "create")
    assert create_event["after"]["tax_id"] == "***redacted***"


def test_tax_id_is_encrypted_at_rest(client, db_session):
    """Regression test, not just an audit-log/API-response check — reads the
    raw column value directly via SQL, bypassing the ORM's decrypting
    EncryptedString TypeDecorator entirely. Guards against a repeat of the
    hardcoded-Fernet-key incident (issue #2): CTO's non-blocking review ask,
    2026-08-07, on this same PR.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Encrypted AG",
        "taxId": "CHE-999.888.776",
        "language": "de",
        "emails": [{"emailType": "work", "emailAddress": "encrypted@example.ch"}],
    }
    created = client.post("/v1/customers", json=payload, headers=_bearer(token)).json()

    raw_value = db_session.execute(
        text("SELECT tax_id FROM customer WHERE id = :id"), {"id": created["id"]}
    ).scalar_one()

    assert raw_value is not None
    assert "CHE-999.888.776" not in raw_value
    assert raw_value.startswith("gAAAAA")  # Fernet token prefix (base64 of version byte 0x80)


# --- Customer PRD Phase A: CustomerPhone / CustomerEmail ---------------------


def test_first_phone_is_auto_primary(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones1@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567", "isPrimary": False},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["isPrimary"] is True


def test_second_phone_of_same_type_not_auto_primary_unless_requested(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones2@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    )
    second = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41442222222"},
        headers=_bearer(token),
    )
    assert second.json()["isPrimary"] is False


def test_first_phone_of_a_new_type_is_auto_primary_even_when_another_type_already_has_one(client):
    """ADR-067 (WP-3 PR-5): is_primary is scoped to (customer, type), not the
    whole customer — a first-of-its-type phone still gets the "first one
    wins" default even though a different type already has a primary.
    """

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones2b@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    )
    second = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "work", "phoneE164": "+41442222222"},
        headers=_bearer(token),
    )
    assert second.json()["isPrimary"] is True


def test_marking_new_phone_of_same_type_primary_unsets_previous(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones3@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    first = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    ).json()
    assert first["isPrimary"] is True

    second = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41442222222", "isPrimary": True},
        headers=_bearer(token),
    ).json()
    assert second["isPrimary"] is True

    listed = client.get(f"/v1/customers/{customer['id']}/phones", headers=_bearer(token)).json()["items"]
    first_now = next(p for p in listed if p["id"] == first["id"])
    assert first_now["isPrimary"] is False


def test_marking_a_different_type_phone_primary_does_not_unset_other_types(client):
    """ADR-067: a mobile primary and a work primary coexist — marking one
    type's phone primary must not touch a different type's primary.
    """

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones3b@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    mobile = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    ).json()
    assert mobile["isPrimary"] is True

    work = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "work", "phoneE164": "+41442222222", "isPrimary": True},
        headers=_bearer(token),
    ).json()
    assert work["isPrimary"] is True

    listed = client.get(f"/v1/customers/{customer['id']}/phones", headers=_bearer(token)).json()["items"]
    mobile_now = next(p for p in listed if p["id"] == mobile["id"])
    assert mobile_now["isPrimary"] is True


def test_duplicate_phone_on_same_customer_is_conflict(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones4@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    response = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "work", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    assert response.status_code == 409


def test_delete_phone(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones5@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phone = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    ).json()
    response = client.delete(f"/v1/customers/{customer['id']}/phones/{phone['id']}", headers=_bearer(token))
    assert response.status_code == 204
    listed = client.get(f"/v1/customers/{customer['id']}/phones", headers=_bearer(token)).json()["items"]
    assert listed == []


def test_customer_email_crud_and_primary_flag(client):
    dealer_id = _create_dealer(client)
    # The customer already owns a primary email from creation, so an email
    # added afterwards is not auto-primary — that only applies to the first.
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    first = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "personal", "emailAddress": "personal@example.ch"},
        headers=_bearer(token),
    ).json()
    assert first["isPrimary"] is False

    second = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "work", "emailAddress": "work@example.ch", "isPrimary": True},
        headers=_bearer(token),
    ).json()
    assert second["isPrimary"] is True

    dup = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "personal", "emailAddress": "work@example.ch"},
        headers=_bearer(token),
    )
    assert dup.status_code == 409

    update = client.patch(
        f"/v1/customers/{customer['id']}/emails/{first['id']}",
        json={"emailAddress": "renamed@example.ch"},
        headers=_bearer(token),
    )
    assert update.status_code == 200
    assert update.json()["emailAddress"] == "renamed@example.ch"

    delete = client.delete(f"/v1/customers/{customer['id']}/emails/{second['id']}", headers=_bearer(token))
    assert delete.status_code == 204


def test_phone_and_email_changes_appear_in_audit_log(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="audit-contacts@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "personal", "emailAddress": "second@example.ch"},
        headers=_bearer(token),
    )
    log = client.get(f"/v1/customers/{customer['id']}/audit-log", headers=_bearer(token)).json()["items"]
    actions = {item["action"] for item in log}
    assert "phone_add" in actions
    assert "email_add" in actions


# --- Customer PRD Phase A: CustomerExternalId (platform_admin-only write) ---


def test_platform_admin_can_create_external_id(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="ext1@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)  # synthetic tenant_id, unrelated to dealer_id
    response = client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-001"},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["systemName"] == "Salesforce"


def test_dealer_admin_cannot_write_external_id(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="ext2@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-002"},
        headers=_bearer(token),
    )
    assert response.status_code == 403


def test_dealer_staff_can_read_external_ids(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="ext3@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-003"},
        headers=_bearer(admin_token),
    )
    dealer_token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{customer['id']}/external-ids", headers=_bearer(dealer_token))
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_external_id_unique_per_tenant_system_and_external_id(client):
    dealer_id = _create_dealer(client)
    customer_a = _create_customer(client, dealer_id, email="ext4a@example.ch")
    customer_b = _create_customer(client, dealer_id, email="ext4b@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/customers/{customer_a['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-DUP"},
        headers=_bearer(admin_token),
    )
    response = client.post(
        f"/v1/customers/{customer_b['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-DUP"},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 409


def test_external_id_one_per_system_per_customer(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="ext5@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-1"},
        headers=_bearer(admin_token),
    )
    response = client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-2"},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 409


def test_external_ids_across_different_tenants_can_collide(client):
    """Different dealers' CRM/OEM ID namespaces legitimately collide — no
    global uniqueness on external_id alone (CTO ruling, 2026-08-07)."""

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    customer_a = _create_customer(client, dealer_a, email="ext6a@example.ch")
    customer_b = _create_customer(client, dealer_b, email="ext6b@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)

    response_a = client.post(
        f"/v1/customers/{customer_a['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SAME-ID"},
        headers=_bearer(admin_token),
    )
    response_b = client.post(
        f"/v1/customers/{customer_b['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SAME-ID"},
        headers=_bearer(admin_token),
    )
    assert response_a.status_code == 201
    assert response_b.status_code == 201


def test_external_id_update_and_delete(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="ext7@example.ch")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    row = client.post(
        f"/v1/customers/{customer['id']}/external-ids",
        json={"systemName": "Salesforce", "externalId": "SF-UPD"},
        headers=_bearer(admin_token),
    ).json()

    update = client.patch(
        f"/v1/customers/{customer['id']}/external-ids/{row['id']}",
        json={"externalId": "SF-UPD-2"},
        headers=_bearer(admin_token),
    )
    assert update.status_code == 200
    assert update.json()["externalId"] == "SF-UPD-2"

    delete = client.delete(
        f"/v1/customers/{customer['id']}/external-ids/{row['id']}", headers=_bearer(admin_token)
    )
    assert delete.status_code == 204


# --- Customer PRD Phase B: numbers, search, validation ----------------------


def test_customer_numbers_are_sequential_per_tenant(client):
    """D-02. Numbers are allocated per tenant and start at 1, so two dealers
    both get K-000001 — the number is only unique *within* a tenant, which is
    what staff expect when they quote it.
    """

    dealer_a = _create_dealer(client)
    first = _create_customer(client, dealer_a)
    second = _create_customer(client, dealer_a, firstName="Beat")
    assert first["customerNumber"] == "K-000001"
    assert second["customerNumber"] == "K-000002"

    dealer_b = _create_dealer(client)
    other_tenant = _create_customer(client, dealer_b)
    assert other_tenant["customerNumber"] == "K-000001"


def test_customer_number_is_server_allocated_and_immutable(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = _create_customer(client, dealer_id, customerNumber="K-999999")
    assert created["customerNumber"] == "K-000001"

    patched = client.patch(
        f"/v1/customers/{created['id']}",
        json={"customerNumber": "K-000042"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert patched.status_code == 200
    assert patched.json()["customerNumber"] == "K-000001"


def test_business_customer_is_findable_by_company_name(client):
    """D-06 — the worst pre-Phase-B gap: search covered first/last name only,
    so a company could not be found by its own name.
    """

    dealer_id = _create_dealer(client)
    _create_customer(
        client, dealer_id,
        customerType="business", companyName="Garage Steinmann AG", firstName=None, lastName=None,
        emails=[{"emailType": "work", "emailAddress": "info@steinmann.ch"}],
    )
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    items = client.get("/v1/customers?q=steinmann", headers=_bearer(token)).json()["items"]
    assert [c["companyName"] for c in items] == ["Garage Steinmann AG"]


def test_customer_is_findable_by_customer_number(client):
    dealer_id = _create_dealer(client)
    created = _create_customer(client, dealer_id)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    items = client.get(f"/v1/customers?q={created['customerNumber']}", headers=_bearer(token)).json()["items"]
    assert [c["id"] for c in items] == [created["id"]]


@pytest.mark.parametrize("typed", ["0791234567", "079 123 45 67", "+41791234567", "0041791234567"])
def test_phone_search_normalises_national_and_international_formats(client, typed):
    """FR-01. A counter clerk types what the customer says; the number is
    stored in E.164. Without normalisation the national format never matched.
    """

    dealer_id = _create_dealer(client)
    created = _create_customer(
        client, dealer_id, phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}]
    )
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    items = client.get("/v1/customers", params={"q": typed}, headers=_bearer(token)).json()["items"]
    assert [c["id"] for c in items] == [created["id"]]


def test_duplicate_check_handles_business_customers(client):
    """D-07. The old candidate shape required first/last name, so a company
    among the candidates raised a serialisation error — duplicate detection
    broke precisely when it found a company.
    """

    dealer_id = _create_dealer(client)
    _create_customer(
        client, dealer_id,
        customerType="business", companyName="Transport Furrer GmbH", firstName=None, lastName=None,
        emails=[{"emailType": "work", "emailAddress": "disposition@furrer.ch"}],
    )
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers/duplicate-check?q=furrer", headers=_bearer(token))
    assert response.status_code == 200, response.text
    candidate = response.json()["items"][0]
    assert candidate["companyName"] == "Transport Furrer GmbH"
    assert candidate["customerType"] == "business"
    assert candidate["firstName"] is None
    assert candidate["match"] == "similar"


def test_merged_customers_are_hidden_from_search_by_default(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, firstName="Marco", lastName="Bernasconi")
    duplicate = _create_customer(client, dealer_id, firstName="Marco", lastName="Bernasconi")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    merged = client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert merged.status_code == 200, merged.text

    default_view = client.get("/v1/customers?q=bernasconi", headers=_bearer(token)).json()["items"]
    assert [c["id"] for c in default_view] == [survivor["id"]]

    with_merged = client.get(
        # Query params are snake_case in this API; only JSON bodies are camelCase.
        "/v1/customers?q=bernasconi&include_merged=true", headers=_bearer(token)
    ).json()["items"]
    assert {c["id"] for c in with_merged} == {survivor["id"], duplicate["id"]}

    # A merged record must also never be offered as a duplicate candidate —
    # it would send the advisor back to the tombstone they just created.
    candidates = client.get(
        "/v1/customers/duplicate-check?q=bernasconi", headers=_bearer(token)
    ).json()["items"]
    assert [c["id"] for c in candidates] == [survivor["id"]]


@pytest.mark.parametrize(
    "tax_id,expected",
    [
        ("CHE-987.654.326", 201),  # valid check digit
        ("CHE-987.654.321", 422),  # wrong check digit — the silent-typo case
        ("CHE-98765432", 422),     # malformed
        ("987.654.326", 422),      # missing CHE prefix
    ],
)
def test_uid_check_digit_is_validated(client, tax_id, expected):
    """D-16. The UID was stored and encrypted but never validated, so a typo
    was persisted silently and only surfaced on an invoice.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Check Digit AG",
        "taxId": tax_id,
        "language": "de",
        "emails": [{"emailType": "work", "emailAddress": "cd@example.ch"}],
    }
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == expected, response.text


def test_foreign_postal_code_is_accepted_but_swiss_rule_still_applies(client):
    """D-11. Cross-border customers (DE/FR/IT/AT/LI) are routine for Swiss
    dealerships; the unconditional 4-digit rule rejected all of them.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    german = _customer_payload(
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Hauptstrasse",
                "addressHouseNumber": "9",
                "addressPostalCode": "79576",
                "addressLocality": "Weil am Rhein",
                "addressCountry": "DE",
            }
        ]
    )
    assert client.post("/v1/customers", json=german, headers=_bearer(token)).status_code == 201

    bad_swiss = _customer_payload(
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Bahnhofstrasse",
                "addressHouseNumber": "1",
                "addressPostalCode": "79576",
                "addressLocality": "Zürich",
                "addressCountry": "CH",
            }
        ]
    )
    assert client.post("/v1/customers", json=bad_swiss, headers=_bearer(token)).status_code == 422


def test_house_number_accepts_a_letter_suffix(client):
    """D-10. The source sheet said digits only, which rejects '12a' — a
    perfectly ordinary Swiss address.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Via Industria",
                "addressHouseNumber": "12a",
                "addressPostalCode": "6828",
                "addressLocality": "Balerna",
                "addressCountry": "CH",
            }
        ]
    )
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    assert response.json()["address"]["addressHouseNumber"] == "12a"


def test_correspondence_language_round_trips_and_is_patchable(client):
    """FR-13. The customer's language is independent of whoever is typing:
    a German-speaking advisor must be able to record an Italian customer.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = _create_customer(client, dealer_id, language="it")
    assert created["language"] == "it"

    patched = client.patch(
        f"/v1/customers/{created['id']}",
        json={"language": "fr"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert patched.status_code == 200
    assert patched.json()["language"] == "fr"

    log = client.get(f"/v1/customers/{created['id']}/audit-log", headers=_bearer(token)).json()["items"]
    update_event = next(item for item in log if item["action"] == "update")
    assert update_event["before"]["language"] == "it"
    assert update_event["after"]["language"] == "fr"


def test_nested_contacts_are_written_atomically_with_the_customer(client):
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = _create_customer(
        client, dealer_id,
        phones=[
            {"phoneType": "work", "phoneE164": "+41447302210"},
            {"phoneType": "mobile", "phoneE164": "+41794128803", "isPrimary": True},
        ],
        emails=[{"emailType": "work", "emailAddress": "info@example.ch"}],
    )
    phones = client.get(f"/v1/customers/{created['id']}/phones", headers=_bearer(token)).json()["items"]
    assert len(phones) == 2
    # Per (customer, type) primary (ADR-067): the work phone is the only one
    # of its type and defaults to primary too, alongside the explicitly
    # requested mobile primary — the two types don't compete.
    assert {p["phoneE164"] for p in phones if p["isPrimary"]} == {"+41447302210", "+41794128803"}


def test_create_is_rolled_back_entirely_when_a_nested_contact_is_invalid(client):
    """The customer and its contact details commit together or not at all —
    no half-created customer that violates its own contact invariant.
    """

    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(
        phones=[
            {"phoneType": "mobile", "phoneE164": "+41791234567"},
            {"phoneType": "landline", "phoneE164": "+41791234567"},
        ]
    )
    assert client.post("/v1/customers", json=payload, headers=_bearer(token)).status_code == 422
    remaining = client.get("/v1/customers", headers=_bearer(token)).json()["items"]
    assert remaining == []


# --- group scope (WP-3 PR-2, ADR-014): a customer is one record per GROUP,
# visible and searchable from every dealership in that group, not just the
# one that created it. Two dealerships in the same group, minted with the
# real dealerGroupId (not the tenant_id-derived shadow value _token() uses
# by default), are what these tests need to prove real cross-dealership
# sharing rather than two tokens that merely happen to compute the same
# derived group_id.


def test_customer_created_by_one_dealership_is_visible_from_a_sister_dealership(client):
    dealer_a = _create_dealer_full(client)
    dealer_b = _create_dealer_full(
        client, dealerGroupId=dealer_a["dealerGroupId"], dealerLicenseNumber="ZH-99999"
    )
    assert dealer_a["dealerGroupId"] == dealer_b["dealerGroupId"]

    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))
    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))

    created = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token_a)).json()

    response = client.get(f"/v1/customers/{created['id']}", headers=_bearer(token_b))
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]

    listed = client.get("/v1/customers", headers=_bearer(token_b)).json()["items"]
    assert any(c["id"] == created["id"] for c in listed)


def test_duplicate_check_is_group_wide(client):
    dealer_a = _create_dealer_full(client)
    dealer_b = _create_dealer_full(
        client, dealerGroupId=dealer_a["dealerGroupId"], dealerLicenseNumber="ZH-99999"
    )
    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))
    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))

    client.post(
        "/v1/customers",
        json=_customer_payload(emails=[{"emailType": "personal", "emailAddress": "shared@example.ch"}]),
        headers=_bearer(token_a),
    )

    response = client.get(
        "/v1/customers/duplicate-check", params={"q": "shared@example.ch"}, headers=_bearer(token_b)
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_customer_number_sequence_is_shared_group_wide(client):
    dealer_a = _create_dealer_full(client)
    dealer_b = _create_dealer_full(
        client, dealerGroupId=dealer_a["dealerGroupId"], dealerLicenseNumber="ZH-99999"
    )
    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))
    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b["id"]), group_id=uuid.UUID(dealer_a["dealerGroupId"]))

    first = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token_a)).json()
    second = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token_b)).json()

    assert first["customerNumber"] != second["customerNumber"]


def test_customer_is_not_visible_across_different_groups(client):
    """Two unrelated dealerships (no shared dealerGroupId) — the WP-3 PR-2
    exit criterion: a cross-group read is impossible, 404 not 403.
    """

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client, dealerLicenseNumber="ZH-88888")
    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a))
    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b))

    created = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token_a)).json()

    response = client.get(f"/v1/customers/{created['id']}", headers=_bearer(token_b))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
