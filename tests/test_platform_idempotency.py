"""KAN-119, platform: every POST honours Idempotency-Key.

Per route: the same key twice makes one record and answers the same; the same
key with a different request is a 409. The mechanism itself (concurrent
twins, release on failure, authorisation before replay) is pinned in
tests/test_idempotent_route.py; the auth routes are exempt
(tests/architecture/test_every_post_accepts_idempotency_key.py).
"""

import uuid

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.platform.models.dealership import DealerGroup, Dealership
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.models.user import User

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _headers(
    role: AccessRole | None = AccessRole.PLATFORM_ADMIN,
    *,
    tenant_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    key: str | None = None,
) -> dict[str, str]:
    tenant_id = tenant_id or uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group_id or uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
    )
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _dealership_payload(**overrides) -> dict:
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


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


# --- POST /v1/dealerships ---------------------------------------------------


def test_create_dealership_twice_under_one_key_makes_one_dealership(client, db_session):
    headers = _headers(key=str(uuid.uuid4()))
    first = client.post("/v1/dealerships", json=_dealership_payload(), headers=headers)
    second = client.post("/v1/dealerships", json=_dealership_payload(), headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert second.json() == first.json()
    assert _count(db_session, Dealership) == 1


def test_create_dealership_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    headers = _headers(key=str(uuid.uuid4()))
    assert client.post("/v1/dealerships", json=_dealership_payload(), headers=headers).status_code == 201

    response = client.post("/v1/dealerships", json=_dealership_payload(legalName="Andere AG"), headers=headers)

    assert response.status_code == 409
    assert _count(db_session, Dealership) == 1


# --- POST /v1/dealerships/{id}/users ----------------------------------------


def _dealership(client) -> dict:
    response = client.post("/v1/dealerships", json=_dealership_payload(), headers=_headers())
    assert response.status_code == 201, response.text
    return response.json()


def _user_payload(**overrides) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "email": "anna@example.ch",
        "role": "admin",
        "accessRoles": ["sales"],
        "isDealerManager": True,
        "authIdentityId": "stub-sub-anna",
    }
    payload.update(overrides)
    return payload


def test_create_user_twice_under_one_key_makes_one_user(client, db_session):
    dealership = _dealership(client)
    headers = _headers(tenant_id=uuid.UUID(dealership["id"]), key=str(uuid.uuid4()))
    path = f"/v1/dealerships/{dealership['id']}/users"

    first = client.post(path, json=_user_payload(), headers=headers)
    second = client.post(path, json=_user_payload(), headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert second.json() == first.json()
    assert _count(db_session, User, User.tenant_id == uuid.UUID(dealership["id"])) == 1


def test_create_user_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    dealership = _dealership(client)
    headers = _headers(tenant_id=uuid.UUID(dealership["id"]), key=str(uuid.uuid4()))
    path = f"/v1/dealerships/{dealership['id']}/users"
    assert client.post(path, json=_user_payload(), headers=headers).status_code == 201

    response = client.post(
        path, json=_user_payload(email="ben@example.ch", authIdentityId="stub-sub-ben"), headers=headers
    )

    assert response.status_code == 409
    assert _count(db_session, User, User.tenant_id == uuid.UUID(dealership["id"])) == 1


# --- POST /v1/reference-data/{list_code} ------------------------------------


def _value_payload(**overrides) -> dict:
    payload = {"valueCode": "diesel", "labelDe": "Diesel", "labelFr": "Diesel", "labelIt": "Diesel", "labelEn": "Diesel"}
    payload.update(overrides)
    return payload


def _seed_list(db_session, list_code: str) -> ReferenceList:
    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.commit()
    return ref_list


def test_create_reference_value_twice_under_one_key_makes_one_value(client, db_session):
    ref_list = _seed_list(db_session, "fuel_type")
    headers = _headers(key=str(uuid.uuid4()))

    first = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)
    second = client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert second.json() == first.json()
    assert _count(db_session, ReferenceValue, ReferenceValue.list_id == ref_list.id) == 1


def test_create_reference_value_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    ref_list = _seed_list(db_session, "fuel_type")
    headers = _headers(key=str(uuid.uuid4()))
    assert client.post("/v1/reference-data/fuel_type", json=_value_payload(), headers=headers).status_code == 201

    response = client.post(
        "/v1/reference-data/fuel_type", json=_value_payload(valueCode="petrol", labelDe="Benzin"), headers=headers
    )

    assert response.status_code == 409
    assert _count(db_session, ReferenceValue, ReferenceValue.list_id == ref_list.id) == 1


# --- POST /v1/dealer-groups/{id}/enable-group-read --------------------------


def _group_with_a_basis(client) -> tuple[dict, dict[str, str]]:
    """A dealership whose group has a recorded legal basis (ADR-030), and a
    platform_admin token in it."""

    dealership = _dealership(client)
    tenant_id, group_id = uuid.UUID(dealership["id"]), uuid.UUID(dealership["dealerGroupId"])
    manager_token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=group_id, roles=frozenset(), is_dealer_manager=True
    )
    manager = {"Authorization": f"Bearer {manager_token}"}
    customer = client.post(
        "/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Muster",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": "anna@example.ch"}],
        },
        headers=manager,
    )
    assert customer.status_code == 201, customer.text
    admin = _headers(tenant_id=tenant_id, group_id=group_id)
    basis = client.post(
        f"/v1/customers/{customer.json()['id']}/legal-basis",
        json={"basis": "joint_controller_agreement", "scope": "x", "sourceDocument": "y"},
        headers=admin,
    )
    assert basis.status_code == 201, basis.text
    return dealership, admin


def test_enable_group_read_retried_under_one_key_runs_once(client, db_session):
    dealership, admin = _group_with_a_basis(client)
    headers = {**admin, "Idempotency-Key": str(uuid.uuid4())}
    path = f"/v1/dealer-groups/{dealership['dealerGroupId']}/enable-group-read"

    first = client.post(path, headers=headers)
    second = client.post(path, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert second.json() == first.json()
    db_session.expire_all()
    group = db_session.get(DealerGroup, uuid.UUID(dealership["dealerGroupId"]))
    assert group is not None and group.group_read_enabled
    # The flip bumps the version on every run: one bump means it ran once.
    assert group.version == first.json()["version"]
    assert client.post(path, headers=admin).json()["version"] == group.version + 1


def test_enable_group_read_with_a_reused_key_on_another_group_is_a_409(client, db_session):
    dealership, admin = _group_with_a_basis(client)
    other, _ = _group_with_a_basis(client)
    headers = {**admin, "Idempotency-Key": str(uuid.uuid4())}
    assert client.post(f"/v1/dealer-groups/{dealership['dealerGroupId']}/enable-group-read", headers=headers).status_code == 200

    response = client.post(f"/v1/dealer-groups/{other['dealerGroupId']}/enable-group-read", headers=headers)

    assert response.status_code == 409
    db_session.expire_all()
    assert not db_session.get(DealerGroup, uuid.UUID(other["dealerGroupId"])).group_read_enabled
