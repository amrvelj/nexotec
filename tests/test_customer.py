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


def _customer_payload(**overrides):
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": "anna@example.ch",
    }
    payload.update(overrides)
    return payload


def _create_customer(client, dealer_id: str, **overrides):
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN)  # random tenant_id, no real Dealer row
    response = client.post("/v1/customers", json=_customer_payload(), headers=_bearer(token))
    assert response.status_code == 404


# --- field validation ---------------------------------------------------------


def test_email_or_phone_required(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(email=None)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_phone_only_is_sufficient(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = _customer_payload(email=None, phone="+41791234567")
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text


def test_invalid_email_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(email="not-an-email"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_invalid_phone_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(phone="0791234567"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_cannot_create_customer_already_merged(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(lifecycleStatus="merged"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_duplicate_email_within_tenant_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    _create_customer(client, dealer_id, email="dup@example.ch")
    response = client.post(
        "/v1/customers", json=_customer_payload(email="dup@example.ch"), headers=_bearer(token)
    )
    assert response.status_code == 409


def test_duplicate_email_across_tenants_is_allowed(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_customer(client, dealer_a, email="shared@example.ch")
    response = client.post(
        "/v1/customers",
        json=_customer_payload(email="shared@example.ch"),
        headers=_bearer(_token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))),
    )
    assert response.status_code == 201


def test_address_round_trips(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(client, dealer_id, address=VALID_ADDRESS)
    assert body["address"]["locality"] == "Zürich"
    assert body["address"]["country"] == "CH"


def test_no_address_at_creation_returns_null(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(client, dealer_id)
    assert body["address"] is None


# --- cross-tenant isolation ---------------------------------------------------


def test_get_customer_cross_tenant_is_404(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant_id
    response = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(other_token))
    assert response.status_code == 404


def test_get_unknown_customer_is_404(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{uuid.uuid4()}", headers=_bearer(token))
    assert response.status_code == 404


# --- optimistic concurrency ---------------------------------------------------


def test_patch_without_if_match_is_400(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}", json={"lastName": "Muster-Meier"}, headers=_bearer(token)
    )
    assert response.status_code == 400


def test_patch_with_stale_if_match_is_409(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = {**_bearer(token), "If-Match": "1"}

    first = client.patch(f"/v1/customers/{customer['id']}", json={"lastName": "A"}, headers=headers)
    assert first.status_code == 200

    stale = client.patch(f"/v1/customers/{customer['id']}", json={"lastName": "B"}, headers=headers)
    assert stale.status_code == 409


def test_patch_updates_customer_and_bumps_version(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neuname"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["lastName"] == "Neuname"
    assert response.json()["version"] == 2


def test_patch_clearing_email_without_phone_is_rejected(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="only@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"email": None},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 400


def test_patch_clearing_email_with_phone_present_is_allowed(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="only@example.ch", phone="+41791234567")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"email": None},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["email"] is None


def test_patch_cannot_set_lifecycle_status_merged_directly(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

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
    _create_customer(client, dealer_id, firstName="Anna", lastName="Muster", email="anna.muster@example.ch")
    _create_customer(client, dealer_id, firstName="Annalise", lastName="Weber", email="annalise@example.ch")
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))

    response = client.get(
        "/v1/customers/duplicate-check?q=anna.muster@example.ch", headers=_bearer(token)
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["email"] == "anna.muster@example.ch"


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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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

    token_b = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))
    response = client.get("/v1/customers", headers=_bearer(token_b))
    assert response.json()["items"] == []


def test_list_customers_paginates(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Muster Garage AG",
        "legalForm": "ag",
        "taxId": "CHE-987.654.321",
        "email": "info@muster-garage.ch",
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"customerType": "business", "email": "info@muster-garage.ch"}
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_individual_customer_rejects_business_fields(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/customers", json=_customer_payload(companyName="Oops AG"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_patch_cannot_set_business_field_on_individual_customer(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="patch-mix@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"companyName": "Oops AG"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 400


def test_tax_id_is_redacted_in_audit_log(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "customerType": "business",
        "companyName": "Redact AG",
        "taxId": "CHE-111.222.333",
        "email": "redact@example.ch",
    }
    created = client.post("/v1/customers", json=payload, headers=_bearer(token)).json()
    log = client.get(f"/v1/customers/{created['id']}/audit-log", headers=_bearer(token))
    create_event = next(item for item in log.json()["items"] if item["action"] == "create")
    assert create_event["after"]["tax_id"] == "***redacted***"


# --- Customer PRD Phase A: CustomerPhone / CustomerEmail ---------------------


def test_first_phone_is_auto_primary(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones1@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567", "isPrimary": False},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["isPrimary"] is True


def test_second_phone_not_auto_primary_unless_requested(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones2@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    )
    second = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "office", "phoneE164": "+41442222222"},
        headers=_bearer(token),
    )
    assert second.json()["isPrimary"] is False


def test_marking_new_phone_primary_unsets_previous(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones3@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    first = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    ).json()
    assert first["isPrimary"] is True

    second = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "office", "phoneE164": "+41442222222", "isPrimary": True},
        headers=_bearer(token),
    ).json()
    assert second["isPrimary"] is True

    listed = client.get(f"/v1/customers/{customer['id']}/phones", headers=_bearer(token)).json()["items"]
    first_now = next(p for p in listed if p["id"] == first["id"])
    assert first_now["isPrimary"] is False


def test_duplicate_phone_on_same_customer_is_conflict(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones4@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    response = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "office", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    assert response.status_code == 409


def test_delete_phone(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id, email="phones5@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
    customer = _create_customer(client, dealer_id, email="emails1@example.ch")
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    first = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "private", "emailAddress": "personal@example.ch"},
        headers=_bearer(token),
    ).json()
    assert first["isPrimary"] is True

    second = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "business", "emailAddress": "work@example.ch", "isPrimary": True},
        headers=_bearer(token),
    ).json()
    assert second["isPrimary"] is True

    dup = client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "private", "emailAddress": "work@example.ch"},
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791234567"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "private", "emailAddress": "second@example.ch"},
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
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
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
