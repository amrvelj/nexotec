"""WP-6b PR-2: DocumentTemplate CRUD (ADR-044 tier 2)."""

import uuid

from app.core.auth import AccessRole, create_access_token

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


def _create_dealership(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Garage Musterbetrieb AG",
            "dealerLicenseNumber": "ZH-12345",
            "licenseState": "ZH",
            "franchiseType": "independent",
            "address": VALID_ADDRESS,
            "phone": "+41441234567",
            "taxId": "CHE-123.456.789",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_reading_with_no_template_yet_returns_version_zero_not_404(client):
    dealership_id = _create_dealership(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealership_id))

    response = client.get(f"/v1/dealerships/{dealership_id}/document-template", headers=_bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] is None
    assert body["version"] == 0
    assert body["footerTextDe"] is None


def test_first_patch_creates_the_row_with_if_match_zero(client):
    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))

    response = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "Vielen Dank für Ihr Vertrauen."},
        headers={**_bearer(token), "If-Match": "0"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] is not None
    assert body["version"] == 1
    assert body["footerTextDe"] == "Vielen Dank für Ihr Vertrauen."
    assert body["footerTextFr"] is None


def test_creating_with_a_nonzero_if_match_is_a_conflict(client):
    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))

    response = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "x"},
        headers={**_bearer(token), "If-Match": "1"},
    )

    assert response.status_code == 409


def test_second_patch_requires_the_current_version(client):
    dealership_id = _create_dealership(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))

    client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "Erste Version."},
        headers={**_bearer(token), "If-Match": "0"},
    )

    stale = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "Stale."},
        headers={**_bearer(token), "If-Match": "0"},
    )
    assert stale.status_code == 409

    fresh = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "Zweite Version."},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["version"] == 2
    assert fresh.json()["footerTextDe"] == "Zweite Version."


def test_a_bare_functional_role_cannot_write(client):
    dealership_id = _create_dealership(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealership_id))

    response = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "x"},
        headers={**_bearer(token), "If-Match": "0"},
    )

    assert response.status_code == 403


def test_dealer_manager_cannot_write_another_dealerships_template(client):
    dealership_id = _create_dealership(client)
    other_tenant_id = uuid.uuid4()
    token = _token(is_dealer_manager=True, tenant_id=other_tenant_id)

    response = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "x"},
        headers={**_bearer(token), "If-Match": "0"},
    )

    # Cross-tenant existence is never confirmed by a 403 (app.core.tenancy).
    assert response.status_code == 404


def test_platform_admin_can_write_any_dealerships_template(client):
    dealership_id = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.uuid4())

    response = client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json={"footerTextDe": "Von Platform gesetzt."},
        headers={**_bearer(token), "If-Match": "0"},
    )

    assert response.status_code == 200, response.text
