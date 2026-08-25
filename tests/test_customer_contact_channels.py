"""Contact channels become child records (WP-3 PR-5, ADR-067, Customers
FR-07): CustomerAddress CRUD, the six read-model projections, the FR-03
"usable contact point" amendment, and merge behaviour across all three
contact-channel tables. Per-(customer, type) primary defaulting itself is
covered in test_customer.py alongside the rest of CustomerPhone/Email —
this file is scoped to what's new in PR-5.
"""

import uuid

from app.core.auth import AccessRole, create_access_token


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
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
        "address": {
            "street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001",
            "locality": "Zürich", "canton": "ZH",
        },
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


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


# --- CustomerAddress CRUD -----------------------------------------------------


def test_first_address_of_a_type_is_auto_primary(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["isPrimary"] is True


def test_domicile_and_billing_addresses_can_both_be_primary(client):
    """Per-type primary (ADR-067) — different types don't compete."""

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    domicile = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        headers=_bearer(token),
    ).json()
    billing = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "billing", "addressStreet": "Postfach", "addressHouseNumber": "1",
            "addressPostalCode": "8001", "addressLocality": "Zürich",
        },
        headers=_bearer(token),
    ).json()
    assert domicile["isPrimary"] is True
    assert billing["isPrimary"] is True


def test_update_and_delete_customer_address(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    address = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        headers=_bearer(token),
    ).json()

    update = client.patch(
        f"/v1/customers/{customer['id']}/addresses/{address['id']}",
        json={"addressLocality": "Ostermundigen"},
        headers=_bearer(token),
    )
    assert update.status_code == 200, update.text
    assert update.json()["addressLocality"] == "Ostermundigen"

    delete = client.delete(
        f"/v1/customers/{customer['id']}/addresses/{address['id']}", headers=_bearer(token)
    )
    assert delete.status_code == 204

    listed = client.get(f"/v1/customers/{customer['id']}/addresses", headers=_bearer(token)).json()["items"]
    assert listed == []


def test_address_cross_tenant_is_404(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    customer = _create_customer(client, dealer_a)
    token_a = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_a))
    address = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        headers=_bearer(token_a),
    ).json()

    # No single-resource GET exists for an address (same convention as
    # phone/email — only list/PATCH/DELETE), so the cross-tenant check goes
    # through PATCH, which is group-scoped via get_customer_address_or_404.
    token_b = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_b))
    response = client.patch(
        f"/v1/customers/{customer['id']}/addresses/{address['id']}",
        json={"label": "should not reach this row"},
        headers=_bearer(token_b),
    )
    assert response.status_code == 404


# --- six read-model projections (ADR-067) -------------------------------------


def test_six_projections_reflect_primary_rows_per_type(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client,
        dealer_id,
        emails=[{"emailType": "personal", "emailAddress": "personal@example.ch"}],
    )
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "landline", "phoneE164": "+41449998877"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/customers/{customer['id']}/emails",
        json={"emailType": "work", "emailAddress": "work@example.ch"},
        headers=_bearer(token),
    )
    client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        headers=_bearer(token),
    )

    body = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(token)).json()
    assert body["phoneMobile"] == "+41791111111"
    assert body["phoneLandline"] == "+41449998877"
    assert body["phoneWork"] is None
    assert body["email"] == "personal@example.ch"
    assert body["emailSecondary"] == "work@example.ch"
    assert body["address"]["addressLocality"] == "Bern"


def test_projections_appear_in_list_view_too(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    )

    items = client.get("/v1/customers", headers=_bearer(token)).json()["items"]
    listed = next(c for c in items if c["id"] == customer["id"])
    assert listed["phoneMobile"] == "+41791111111"


def test_closed_row_is_excluded_from_projections(client):
    """valid_to marks a row closed — it stays readable but drops out of the
    six projections (ADR-067).
    """

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phone = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41791111111"},
        headers=_bearer(token),
    ).json()
    another = client.post(
        f"/v1/customers/{customer['id']}/phones",
        json={"phoneType": "mobile", "phoneE164": "+41792222222"},
        headers=_bearer(token),
    ).json()
    assert another["isPrimary"] is False

    client.patch(
        f"/v1/customers/{customer['id']}/phones/{phone['id']}",
        json={"validTo": "2020-01-01T00:00:00Z"},
        headers=_bearer(token),
    )

    body = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(token)).json()
    # The closed row was the primary — closing it does not promote the
    # other row automatically (no re-fixup on close), so the projection is
    # simply empty until someone marks a survivor primary.
    assert body["phoneMobile"] is None


# --- FR-03 amendment: only a USABLE contact point counts (ADR-067) -----------


def test_closing_the_last_usable_email_is_rejected(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_email = client.get(f"/v1/customers/{customer['id']}/emails", headers=_bearer(token)).json()["items"][0]

    response = client.patch(
        f"/v1/customers/{customer['id']}/emails/{only_email['id']}",
        json={"validTo": "2020-01-01T00:00:00Z"},
        headers=_bearer(token),
    )
    assert response.status_code == 400


def test_marking_the_last_usable_phone_do_not_use_is_rejected(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client, dealer_id, emails=[], phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}]
    )
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_phone = client.get(f"/v1/customers/{customer['id']}/phones", headers=_bearer(token)).json()["items"][0]

    response = client.patch(
        f"/v1/customers/{customer['id']}/phones/{only_phone['id']}",
        json={"doNotUse": True, "doNotUseReason": "bounced"},
        headers=_bearer(token),
    )
    assert response.status_code == 400


def test_closing_an_email_is_allowed_when_a_usable_phone_remains(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client, dealer_id, phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}]
    )
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_email = client.get(f"/v1/customers/{customer['id']}/emails", headers=_bearer(token)).json()["items"][0]

    response = client.patch(
        f"/v1/customers/{customer['id']}/emails/{only_email['id']}",
        json={"validTo": "2020-01-01T00:00:00Z"},
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text


# --- consent + do-not-use round trip ------------------------------------------


def test_consent_and_do_not_use_fields_round_trip(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client, dealer_id, phones=[{"phoneType": "mobile", "phoneE164": "+41791234567"}]
    )
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    only_email = client.get(f"/v1/customers/{customer['id']}/emails", headers=_bearer(token)).json()["items"][0]

    response = client.patch(
        f"/v1/customers/{customer['id']}/emails/{only_email['id']}",
        json={
            "consentGranted": True,
            "consentSource": "signup-form",
            "label": "Newsletter address",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["consentGranted"] is True
    assert body["consentSource"] == "signup-form"
    assert body["label"] == "Newsletter address"


# --- merge repoints addresses too ---------------------------------------------


def test_merge_repoints_addresses(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, email="survivor-addr@example.ch")
    duplicate = _create_customer(client, dealer_id, email="dup-addr@example.ch")
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    client.post(
        f"/v1/customers/{duplicate['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Dorfstrasse", "addressHouseNumber": "3",
            "addressPostalCode": "3000", "addressLocality": "Bern",
        },
        headers=_bearer(token),
    )

    response = client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text

    addresses = client.get(
        f"/v1/customers/{survivor['id']}/addresses", headers=_bearer(token)
    ).json()["items"]
    assert len(addresses) == 1
    assert addresses[0]["addressLocality"] == "Bern"
    assert addresses[0]["isPrimary"] is True

    log = client.get(f"/v1/customers/{duplicate['id']}/audit-log", headers=_bearer(token)).json()["items"]
    merge_event = next(item for item in log if item["action"] == "merge")
    assert merge_event["after"]["addressesRepointed"] == 1
