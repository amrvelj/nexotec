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


def _dealership_payload(**overrides):
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
    return payload


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


def _create_dealership(client, **overrides):
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post("/v1/dealerships", json=_dealership_payload(**overrides), headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- creation / access control -------------------------------------------


def test_platform_admin_can_create_dealership(client):
    body = _create_dealership(client)
    assert body["status"] == "pending_onboarding"
    assert body["version"] == 1
    assert "taxId" not in body


def test_dealership_creation_defaults_to_a_new_group_of_one(client):
    """Omitting dealerGroupId (the common case — onboarding a standalone
    dealer) creates a fresh dealer_group rather than requiring one upfront.
    """

    body = _create_dealership(client)
    assert body["dealerGroupId"] is not None


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_platform_admin_cannot_create_dealership(client, role):
    token = _token(role)
    response = client.post("/v1/dealerships", json=_dealership_payload(), headers=_bearer(token))
    assert response.status_code == 403


def test_a_dealer_manager_cannot_create_a_dealership(client):
    """is_dealer_manager grants administration of ITS OWN dealership only —
    dealership onboarding is platform_admin-only and the manager flag
    doesn't cross that boundary, same as it doesn't cross the group boundary.
    """
    token = _token(is_dealer_manager=True)
    response = client.post("/v1/dealerships", json=_dealership_payload(), headers=_bearer(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_create_dealership_requires_authentication(client):
    response = client.post("/v1/dealerships", json=_dealership_payload())
    assert response.status_code == 401


# --- field validation ------------------------------------------------------


def test_invalid_canton_code_is_rejected(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = _dealership_payload(licenseState="XX")
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable_entity"


def test_invalid_postal_code_is_rejected(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = _dealership_payload(address={**VALID_ADDRESS, "postalCode": "abc"})
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_invalid_phone_is_rejected(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = _dealership_payload(phone="0441234567")  # missing '+' country code
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 422


# --- tax_id: encrypted at rest, never returned ------------------------------


def test_tax_id_is_never_returned_in_read_response(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.get(f"/v1/dealerships/{body['id']}", headers=_bearer(token))
    assert response.status_code == 200
    assert "taxId" not in response.json()


def test_tax_id_is_encrypted_at_rest(db_session):
    from sqlalchemy import text

    from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType

    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()

    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage AG",
        dealer_license_number="ZH-1",
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        tax_id="CHE-123.456.789",
    )
    db_session.add(dealership)
    db_session.commit()

    raw = db_session.execute(
        text("select tax_id from dealership where id = :id"), {"id": str(dealership.id)}
    ).scalar()
    assert raw != "CHE-123.456.789"
    assert dealership.tax_id == "CHE-123.456.789"  # decrypts transparently through the ORM


# --- cross-tenant isolation --------------------------------------------------


def test_dealer_manager_can_read_own_dealership(client):
    body = _create_dealership(client)
    dealership_id = body["id"]
    own_manager_token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealership_id))
    response = client.get(f"/v1/dealerships/{dealership_id}", headers=_bearer(own_manager_token))
    assert response.status_code == 200


def test_dealer_manager_cannot_read_other_dealership(client):
    body = _create_dealership(client)
    dealership_id = body["id"]
    other_manager_token = _token(is_dealer_manager=True)  # random, different tenant_id
    response = client.get(f"/v1/dealerships/{dealership_id}", headers=_bearer(other_manager_token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_get_unknown_dealership_is_404(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.get(f"/v1/dealerships/{uuid.uuid4()}", headers=_bearer(token))
    assert response.status_code == 404


def test_list_dealerships_is_platform_admin_only(client):
    _create_dealership(client)
    dealer_manager_token = _token(is_dealer_manager=True)
    response = client.get("/v1/dealerships", headers=_bearer(dealer_manager_token))
    assert response.status_code == 403


# --- optimistic concurrency ---------------------------------------------------


def test_patch_without_if_match_is_400(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.patch(f"/v1/dealerships/{body['id']}", json={"status": "active"}, headers=_bearer(token))
    assert response.status_code == 400


def test_patch_with_stale_if_match_is_409(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = {**_bearer(token), "If-Match": "1"}
    first = client.patch(f"/v1/dealerships/{body['id']}", json={"status": "active"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["version"] == 2

    stale = client.patch(f"/v1/dealerships/{body['id']}", json={"status": "suspended"}, headers=headers)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"


def test_patch_updates_dealership_and_bumps_version(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.patch(
        f"/v1/dealerships/{body['id']}",
        json={"legalName": "New Legal Name"},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200
    assert response.json()["legalName"] == "New Legal Name"
    assert response.json()["version"] == 2


# --- lifecycle: offboarded is terminal ----------------------------------------


def test_offboarded_dealership_status_is_terminal(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)

    r1 = client.patch(
        f"/v1/dealerships/{body['id']}", json={"status": "offboarded"}, headers={**headers, "If-Match": "1"}
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "offboarded"

    r2 = client.patch(
        f"/v1/dealerships/{body['id']}", json={"status": "active"}, headers={**headers, "If-Match": "2"}
    )
    assert r2.status_code == 409


# --- audit logging ----------------------------------------------------------


def test_license_and_status_changes_are_audit_logged(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = {**_bearer(token), "If-Match": "1"}
    client.patch(f"/v1/dealerships/{body['id']}", json={"dealerLicenseNumber": "ZH-99999"}, headers=headers)

    log = client.get(f"/v1/dealerships/{body['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200
    items = log.json()["items"]
    actions = [item["action"] for item in items]
    assert "create" in actions
    assert "update" in actions
    update_event = next(item for item in items if item["action"] == "update")
    assert update_event["after"]["dealer_license_number"] == "ZH-99999"


def test_tax_id_audit_entries_are_redacted_not_plaintext(client):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)

    log = client.get(f"/v1/dealerships/{body['id']}/audit-log", headers=_bearer(token))
    create_event = next(item for item in log.json()["items"] if item["action"] == "create")
    assert create_event["after"]["tax_id"] == "***redacted***"


def test_dealership_audit_log_requires_dealer_manager_or_auditor_or_platform_admin(client):
    body = _create_dealership(client)
    sales_token = _token(AccessRole.SALES, tenant_id=uuid.UUID(body["id"]))
    response = client.get(f"/v1/dealerships/{body['id']}/audit-log", headers=_bearer(sales_token))
    assert response.status_code == 403


# --- pagination ---------------------------------------------------------------


def test_list_dealerships_paginates(client):
    for i in range(3):
        _create_dealership(client, legalName=f"Garage {i} AG", dealerLicenseNumber=f"ZH-{i}")

    token = _token(AccessRole.PLATFORM_ADMIN)
    first_page = client.get("/v1/dealerships?limit=2", headers=_bearer(token))
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["nextCursor"] is not None

    second_page = client.get(
        f"/v1/dealerships?limit=2&cursor={first_body['nextCursor']}", headers=_bearer(token)
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["nextCursor"] is None

    first_ids = {item["id"] for item in first_body["items"]}
    second_ids = {item["id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)
