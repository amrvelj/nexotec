"""WP-5 PR-8: master-data admin (brands + mapping-gap queue)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.audit import list_audit_events
from app.core.auth import AccessRole, create_access_token
from app.main import app as fastapi_app
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.provider import MappingGap
from app.vehicle.services.provider import resolve_provider_code


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


def _seed_fuel_type_list(db_session) -> None:
    ref_list = ReferenceList(list_code="fuel_type")
    db_session.add(ref_list)
    db_session.flush()
    for code in ("petrol", "diesel", "electric", "hybrid", "plugin_hybrid", "hydrogen"):
        db_session.add(
            ReferenceValue(list_id=ref_list.id, value_code=code, label_de=code, label_fr=code, label_it=code, label_en=code)
        )
    db_session.commit()


def _open_gap_id(db_session, provider_code: str, *, code_group: str = "fuel_type") -> str:
    # Production key shape: raw FzArt vehicle kind, semantic code group.
    resolve_provider_code(
        db_session, provider="auto_i_dat", vehicle_kind="01", code_group=code_group, provider_code=provider_code
    )
    db_session.commit()  # the client serves requests from a separate session
    return str(db_session.scalar(select(MappingGap.id).where(MappingGap.provider_code == provider_code)))


@pytest.fixture()
def http(client) -> TestClient:
    """The shared `client` fixture re-raises server exceptions into the test;
    this one answers with the 500 an admin would see (KAN-77)."""

    return TestClient(fastapi_app, raise_server_exceptions=False)


def _resolve(http: TestClient, gap_id: str, list_code: str, value_code: str):
    return http.post(
        f"/v1/vehicle-mdm/mapping-gaps/{gap_id}/resolve",
        json={"canonicalListCode": list_code, "canonicalValueCode": value_code},
        headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)),
    )


def test_resolving_a_mapping_gap_via_api(http, db_session):
    _seed_fuel_type_list(db_session)
    _open_gap_id(db_session, "10")

    token = _token(AccessRole.PLATFORM_ADMIN)
    list_response = http.get("/v1/vehicle-mdm/mapping-gaps", headers=_bearer(token))
    assert list_response.status_code == 200, list_response.text
    gap_id = list_response.json()["items"][0]["id"]

    resolve_response = _resolve(http, gap_id, "fuel_type", "hydrogen")
    assert resolve_response.status_code == 200, resolve_response.text
    assert resolve_response.json()["resolved"] is True
    assert resolve_response.json()["resolvedValueCode"] == "hydrogen"


def test_resolving_the_same_gap_twice_is_idempotent_then_a_conflict(http, db_session):
    _seed_fuel_type_list(db_session)
    gap_id = _open_gap_id(db_session, "12")

    first = _resolve(http, gap_id, "fuel_type", "plugin_hybrid")
    assert first.status_code == 200, first.text

    # A double click: same answer, nothing changes.
    again = _resolve(http, gap_id, "fuel_type", "plugin_hybrid")
    assert again.status_code == 200, again.text
    assert again.json()["resolvedAt"] == first.json()["resolvedAt"]
    assert len(list_audit_events(db_session, entity_type="vehicle_mapping_gap", entity_id=uuid.UUID(gap_id), tenant_id=None)) == 1

    # A second admin with a different answer: refused, naming the mapping.
    other = _resolve(http, gap_id, "fuel_type", "diesel")
    assert other.status_code == 409, other.text
    assert "fuel_type/plugin_hybrid" in other.json()["error"]["message"]


@pytest.mark.parametrize(
    ("list_code", "value_code"),
    [("transmission", "manual"), ("fuel_type", "unicorn")],
)
def test_resolving_into_anything_but_the_gaps_own_active_list_is_422(http, db_session, list_code, value_code):
    _seed_fuel_type_list(db_session)
    gap_id = _open_gap_id(db_session, "12")

    response = _resolve(http, gap_id, list_code, value_code)
    assert response.status_code == 422, response.text


def test_a_gap_with_no_reference_list_is_409_not_500(http, db_session):
    gap_id = _open_gap_id(db_session, "7", code_group="011")

    response = _resolve(http, gap_id, "011", "petrol")
    assert response.status_code == 409, response.text
    assert "no reference list" in response.json()["error"]["message"]
