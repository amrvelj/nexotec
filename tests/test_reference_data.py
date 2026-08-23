import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.platform.models.reference_data import ReferenceList


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_list(db_session, list_code: str) -> ReferenceList:
    """ReferenceList rows are seed-only for v1 (no create endpoint — see
    app/models/reference_data.py), so tests seed directly via the ORM the
    way a real deploy would via the alembic migration.
    """

    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.commit()
    db_session.refresh(ref_list)
    return ref_list


def _value_payload(**overrides):
    payload = {
        "valueCode": "diesel",
        "labelDe": "Diesel",
        "labelFr": "Diesel",
        "labelIt": "Diesel",
        "labelEn": "Diesel",
    }
    payload.update(overrides)
    return payload


# --- creation / access control ---------------------------------------------


def test_platform_admin_can_create_reference_value(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(token))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["listCode"] == "fuel_type"
    assert body["valueCode"] == "diesel"
    assert body["active"] is True
    assert body["sortOrder"] == 0
    assert body["version"] == 1


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_platform_admin_cannot_create_reference_value(client, db_session, role):
    """Writes are platform_admin-only (CTO ruling, 2026-08-06): this table
    has no tenant partition, so a dealer-manager write would have blast
    radius across every other tenant's dropdowns.
    """

    _seed_list(db_session, "fuel_type")
    token = _token(role)
    response = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(token))
    assert response.status_code == 403


def test_a_dealer_manager_cannot_create_a_reference_value(client, db_session):
    """The manager flag grants write within its own dealership only — the
    canonical taxonomy isn't tenant-owned at all, so it never crosses that
    boundary either.
    """
    _seed_list(db_session, "fuel_type")
    token = _token(is_dealer_manager=True)
    response = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_create_reference_value_requires_authentication(client, db_session):
    _seed_list(db_session, "fuel_type")
    response = client.post("/v1/reference-data/fuel_type", json=_value_payload())
    assert response.status_code == 401


def test_create_under_unknown_list_code_is_404(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post("/v1/reference-data/not_a_real_list", json=_value_payload(), headers=_bearer(token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_duplicate_value_code_in_same_list_is_rejected(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    first = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)
    assert first.status_code == 201

    dup = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"


def test_same_value_code_allowed_in_different_lists(client, db_session):
    _seed_list(db_session, "fuel_type")
    _seed_list(db_session, "vehicle_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)

    first = client.post("/v1/reference-data/fuel_type", json=_value_payload(valueCode="other"), headers=headers)
    second = client.post("/v1/reference-data/vehicle_type", json=_value_payload(valueCode="other"), headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201


# --- read: open to any authenticated role -----------------------------------


def test_any_authenticated_role_can_list_reference_values(client, db_session):
    _seed_list(db_session, "fuel_type")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(admin_token))

    sales_token = _token(AccessRole.SALES)
    response = client.get("/v1/reference-data/fuel_type", headers=_bearer(sales_token))
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["valueCode"] == "diesel"


def test_list_reference_values_requires_authentication(client, db_session):
    _seed_list(db_session, "fuel_type")
    response = client.get("/v1/reference-data/fuel_type")
    assert response.status_code == 401


def test_list_unknown_list_code_is_404(client):
    token = _token(AccessRole.SALES)
    response = client.get("/v1/reference-data/not_a_real_list", headers=_bearer(token))
    assert response.status_code == 404


def test_list_reference_values_filters_by_active(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(valueCode="petrol"), headers=headers)
    inactive = client.post(
        "/v1/reference-data/fuel_type", json=_value_payload(valueCode="hydrogen"), headers=headers
    )
    client.patch(
        "/v1/reference-data/fuel_type/hydrogen",
        json={"active": False},
        headers={**headers, "If-Match": "1"},
    )
    assert inactive.status_code == 201

    response = client.get("/v1/reference-data/fuel_type?active=true", headers=headers)
    codes = {item["valueCode"] for item in response.json()["items"]}
    assert codes == {"petrol"}


# --- optimistic concurrency ---------------------------------------------------


def test_patch_without_if_match_is_400(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)

    response = client.patch("/v1/reference-data/fuel_type/diesel", json={"active": False}, headers=headers)
    assert response.status_code == 400


def test_patch_with_stale_if_match_is_409(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)

    first = client.patch(
        "/v1/reference-data/fuel_type/diesel", json={"active": False}, headers={**headers, "If-Match": "1"}
    )
    assert first.status_code == 200
    assert first.json()["version"] == 2

    stale = client.patch(
        "/v1/reference-data/fuel_type/diesel", json={"active": True}, headers={**headers, "If-Match": "1"}
    )
    assert stale.status_code == 409


def test_patch_updates_labels_and_bumps_version(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)

    response = client.patch(
        "/v1/reference-data/fuel_type/diesel",
        json={"labelDe": "Diesel (fossil)", "sortOrder": 5},
        headers={**headers, "If-Match": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["labelDe"] == "Diesel (fossil)"
    assert body["sortOrder"] == 5
    assert body["version"] == 2


def test_patch_unknown_value_code_is_404(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.patch(
        "/v1/reference-data/fuel_type/not_a_real_value",
        json={"active": False},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AUDITOR])
def test_non_platform_admin_cannot_patch_reference_value(client, db_session, role):
    _seed_list(db_session, "fuel_type")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(admin_token))

    token = _token(role)
    response = client.patch(
        "/v1/reference-data/fuel_type/diesel", json={"active": False}, headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 403


def test_a_dealer_manager_cannot_patch_a_reference_value(client, db_session):
    _seed_list(db_session, "fuel_type")
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=_bearer(admin_token))

    token = _token(is_dealer_manager=True)
    response = client.patch(
        "/v1/reference-data/fuel_type/diesel", json={"active": False}, headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 403


# --- audit logging ------------------------------------------------------------


def test_create_and_update_are_audit_logged(client, db_session):
    from app.core.audit import list_audit_events

    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    created = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)
    value_id = created.json()["id"]

    client.patch(
        "/v1/reference-data/fuel_type/diesel",
        json={"active": False},
        headers={**headers, "If-Match": "1"},
    )

    events = list_audit_events(
        db_session, entity_type="reference_value", entity_id=uuid.UUID(value_id), tenant_id=None
    )
    actions = [e.action for e in events]
    assert "create" in actions
    assert "update" in actions
    update_event = next(e for e in events if e.action == "update")
    assert update_event.after["active"] is False


# --- pagination ---------------------------------------------------------------


def test_list_reference_values_paginates(client, db_session):
    _seed_list(db_session, "fuel_type")
    token = _token(AccessRole.PLATFORM_ADMIN)
    headers = _bearer(token)
    for code in ["petrol", "diesel", "electric"]:
        client.post("/v1/reference-data/fuel_type", json=_value_payload(valueCode=code), headers=headers)

    first_page = client.get("/v1/reference-data/fuel_type?limit=2", headers=headers)
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["nextCursor"] is not None

    second_page = client.get(
        f"/v1/reference-data/fuel_type?limit=2&cursor={first_body['nextCursor']}", headers=headers
    )
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["nextCursor"] is None
