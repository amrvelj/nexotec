"""WP-5 PR-8: master-data admin (brands + mapping-gap queue)."""

import uuid

from app.core.auth import AccessRole, create_access_token


def _token(role: AccessRole | None = None) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=False,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_any_authenticated_user_can_list_brands(client):
    response = client.get("/v1/vehicle-mdm/brands", headers=_bearer(_token()))
    assert response.status_code == 200, response.text


def test_only_platform_admin_can_create_brand(client):
    response = client.post(
        "/v1/vehicle-mdm/brands", json={"code": "alfa-romeo", "displayName": "Alfa Romeo"},
        headers=_bearer(_token(AccessRole.SALES)),
    )
    assert response.status_code == 403, response.text


def test_platform_admin_can_create_and_update_brand(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    create_response = client.post(
        "/v1/vehicle-mdm/brands", json={"code": "alfa-romeo", "displayName": "Alfa Romeo"}, headers=_bearer(token)
    )
    assert create_response.status_code == 201, create_response.text
    brand_id = create_response.json()["id"]

    update_response = client.patch(
        f"/v1/vehicle-mdm/brands/{brand_id}", json={"displayName": "Alfa Romeo S.p.A."},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["displayName"] == "Alfa Romeo S.p.A."


def test_duplicate_brand_code_is_conflict(client):
    token = _token(AccessRole.PLATFORM_ADMIN)
    client.post("/v1/vehicle-mdm/brands", json={"code": "dup", "displayName": "One"}, headers=_bearer(token))
    response = client.post("/v1/vehicle-mdm/brands", json={"code": "dup", "displayName": "Two"}, headers=_bearer(token))
    assert response.status_code == 409, response.text


def test_mapping_gap_queue_is_platform_admin_only(client):
    response = client.get("/v1/vehicle-mdm/mapping-gaps", headers=_bearer(_token(AccessRole.SALES)))
    assert response.status_code == 403, response.text


def test_resolving_a_mapping_gap_via_api(client, db_session):
    from app.vehicle.services.provider import resolve_provider_code

    resolve_provider_code(db_session, provider="auto-i-dat", vehicle_kind="passenger_car", code_group="011", provider_code="7")
    db_session.commit()  # the client fixture serves requests from a separate session

    token = _token(AccessRole.PLATFORM_ADMIN)
    list_response = client.get("/v1/vehicle-mdm/mapping-gaps", headers=_bearer(token))
    assert list_response.status_code == 200, list_response.text
    gap_id = list_response.json()["items"][0]["id"]

    resolve_response = client.post(
        f"/v1/vehicle-mdm/mapping-gaps/{gap_id}/resolve",
        json={"canonicalListCode": "fuel_type", "canonicalValueCode": "hydrogen"},
        headers=_bearer(token),
    )
    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["resolved"] is True
