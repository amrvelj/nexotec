"""Canton derivation from postal code (D-13)."""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.core.postal_codes import derive_canton


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


# --- derive_canton unit tests ---------------------------------------------------


def test_derive_canton_known_postal_code():
    assert derive_canton("8001", "CH") == "ZH"
    assert derive_canton("1000", "CH") == "VD"


def test_derive_canton_ambiguous_postal_code_is_none():
    # 1290 Versoix straddles VD/GE — deliberately excluded from the table.
    assert derive_canton("1290", "CH") is None


def test_derive_canton_non_swiss_country_is_none():
    assert derive_canton("8001", "DE") is None
    assert derive_canton("8001", None) is None


def test_derive_canton_missing_postal_code_is_none():
    assert derive_canton(None, "CH") is None
    assert derive_canton("", "CH") is None


def test_derive_canton_unknown_postal_code_is_none():
    assert derive_canton("0000", "CH") is None


# --- API wiring ------------------------------------------------------------------


def test_create_customer_with_swiss_address_derives_canton(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(
        client,
        dealer_id,
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Bahnhofstrasse",
                "addressHouseNumber": "1",
                "addressPostalCode": "8001",
                "addressLocality": "Zürich",
            }
        ],
    )
    assert body["address"]["addressCanton"] == "ZH"


def test_create_customer_with_foreign_address_canton_is_null(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(
        client,
        dealer_id,
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Hauptstrasse",
                "addressHouseNumber": "1",
                "addressPostalCode": "10115",
                "addressLocality": "Berlin",
                "addressCountry": "DE",
            }
        ],
    )
    assert body["address"]["addressCanton"] is None


def test_create_customer_with_ambiguous_postal_code_canton_is_null(client):
    dealer_id = _create_dealer(client)
    body = _create_customer(
        client,
        dealer_id,
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Rue de Lyon",
                "addressHouseNumber": "1",
                "addressPostalCode": "1290",
                "addressLocality": "Versoix",
            }
        ],
    )
    assert body["address"]["addressCanton"] is None


def test_adding_a_customer_address_derives_canton(client):
    """Address is no longer a CustomerUpdate field (WP-3 PR-5, ADR-067) — it
    is managed through its own child-row endpoint.
    """

    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    assert customer["address"] is None

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile",
            "addressStreet": "Marktgasse",
            "addressHouseNumber": "10",
            "addressPostalCode": "3011",
            "addressLocality": "Bern",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["addressCanton"] == "BE"


def test_removing_the_only_customer_address_clears_the_projection(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(
        client,
        dealer_id,
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Bahnhofstrasse",
                "addressHouseNumber": "1",
                "addressPostalCode": "8001",
                "addressLocality": "Zürich",
            }
        ],
    )
    assert customer["address"]["addressCanton"] == "ZH"

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    address_id = customer["address"]["id"]
    response = client.delete(
        f"/v1/customers/{customer['id']}/addresses/{address_id}", headers=_bearer(token)
    )
    assert response.status_code == 204

    refreshed = client.get(f"/v1/customers/{customer['id']}", headers=_bearer(token))
    assert refreshed.json()["address"] is None
