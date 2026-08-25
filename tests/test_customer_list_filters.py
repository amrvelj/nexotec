"""List filters (FR-02): `?customerType=`, `?language=`, `?canton=` on the
customer list endpoint, on top of the existing `lifecycleStatus`/`updatedSince`
filters. Combined-filter and zero-result cases matter most here — each filter
clause is a trivial `.where()`, so the risk is in how they compose, not in any
one clause individually.
"""

import uuid

from app.core.auth import AccessRole, create_access_token

ZH_ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}
VD_ADDRESS = {"street": "Rue de Bourg", "houseNumber": "1", "postalCode": "1000", "locality": "Lausanne", "canton": "VD"}


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
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
        "legalName": "Garage Musterbetrieb AG", "dealerLicenseNumber": "ZH-12345", "licenseState": "ZH",
        "franchiseType": "independent", "address": ZH_ADDRESS, "phone": "+41441234567",
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


def _list(client, dealer_id: str, **query):
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params=query, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


def _business_email() -> dict:
    return {"emailType": "business", "emailAddress": f"biz-{uuid.uuid4().hex[:8]}@example.ch"}


# --- customerType --------------------------------------------------------------------


def test_filter_by_customer_type_individual(client):
    dealer_id = _create_dealer(client)
    individual = _create_customer(client, dealer_id, lastName="Solo")
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual")
    ids = [c["id"] for c in body["items"]]
    assert ids == [individual["id"]]


def test_filter_by_customer_type_business(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Solo")
    business = _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="business")
    ids = [c["id"] for c in body["items"]]
    assert ids == [business["id"]]


# --- language --------------------------------------------------------------------


def test_filter_by_language(client):
    dealer_id = _create_dealer(client)
    de_customer = _create_customer(client, dealer_id, lastName="Deutsch", language="de")
    _create_customer(client, dealer_id, lastName="Français", language="fr")

    body = _list(client, dealer_id, language="de")
    ids = [c["id"] for c in body["items"]]
    assert ids == [de_customer["id"]]


# --- canton (derived from address postal code) ------------------------------------------


def test_filter_by_canton(client):
    dealer_id = _create_dealer(client)
    zh_customer = _create_customer(client, dealer_id, lastName="Zurich", address=ZH_ADDRESS)
    _create_customer(client, dealer_id, lastName="Vaud", address=VD_ADDRESS)

    body = _list(client, dealer_id, canton="ZH")
    ids = [c["id"] for c in body["items"]]
    assert ids == [zh_customer["id"]]


# --- combined filters -----------------------------------------------------------------


def test_combined_filters_narrow_correctly(client):
    dealer_id = _create_dealer(client)
    target = _create_customer(client, dealer_id, lastName="Match", language="fr", address=VD_ADDRESS)
    _create_customer(client, dealer_id, lastName="WrongLanguage", language="de", address=VD_ADDRESS)
    _create_customer(client, dealer_id, lastName="WrongCanton", language="fr", address=ZH_ADDRESS)
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None, companyName="Beta AG",
        language="fr", address=VD_ADDRESS, emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual", language="fr", canton="VD")
    ids = [c["id"] for c in body["items"]]
    assert ids == [target["id"]]


def test_combined_filters_with_no_matches_returns_empty(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Deutsch", language="de", address=ZH_ADDRESS)

    body = _list(client, dealer_id, customer_type="business", language="it", canton="GE")
    assert body["items"] == []
    assert body["total"] == 0
