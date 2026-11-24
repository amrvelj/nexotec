"""KAN-266 step 2 (contact): every contact POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the transition once)
and answers the same; the same key with a different request is a 409. The
mechanism itself is pinned in tests/core/test_idempotent_route.py.
"""

import random
import uuid

from sqlalchemy import func, select

from app.contact.models.contact import Contact, ContactAddress, ContactEmail, ContactExternalId, ContactPhone
from app.contact.models.legal_basis import LegalBasis
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(tenant_id: uuid.UUID, group_id: uuid.UUID | None = None, *, role: AccessRole | None = None) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=group_id or dealer_group_of(str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=role is None,
    )


def _headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _dealership(client) -> dict:
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post(
        "/v1/dealerships", json=payload, headers=_headers(_token(uuid.uuid4(), role=AccessRole.PLATFORM_ADMIN))
    )
    assert response.status_code == 201, response.text
    return response.json()


def _contact_payload(**overrides) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    return payload


def _setup(client) -> tuple[dict, str]:
    """A dealership and a manager token in it."""

    dealership = _dealership(client)
    return dealership, _token(uuid.UUID(dealership["id"]), uuid.UUID(dealership["dealerGroupId"]))


def _admin(dealership: dict) -> str:
    return _token(uuid.UUID(dealership["id"]), uuid.UUID(dealership["dealerGroupId"]), role=AccessRole.PLATFORM_ADMIN)


def _contact(client, token: str, **overrides) -> dict:
    response = client.post("/v1/contacts", json=_contact_payload(**overrides), headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict (a stale If-Match)."""

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["idempotencyKey"] == key


def _twice(client, path: str, token: str, body: dict | None, **extra: str):
    key = str(uuid.uuid4())
    first = client.post(path, json=body, headers=_headers(token, key, **extra))
    second = client.post(path, json=body, headers=_headers(token, key, **extra))
    assert first.status_code in (200, 201), first.text
    assert second.status_code == first.status_code
    assert second.json() == first.json()
    return key, first.json()


# --- POST /v1/contacts ----------------------------------------------------


def test_create_contact_twice_under_one_key_makes_one_contact(client, db_session):
    _, token = _setup(client)
    body = _contact_payload()

    _twice(client, "/v1/contacts", token, body)

    assert _count(db_session, Contact) == 1


def test_create_contact_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    _, token = _setup(client)
    key, _ = _twice(client, "/v1/contacts", token, _contact_payload())

    response = client.post("/v1/contacts", json=_contact_payload(firstName="Berta"), headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, Contact) == 1


# --- contact channels and external ids ---------------------------------------

_CHILD_CREATES = [
    (
        "phones",
        ContactPhone,
        {"phoneType": "mobile", "phoneE164": "+41791111111"},
        {"phoneType": "mobile", "phoneE164": "+41792222222"},
    ),
    (
        "emails",
        ContactEmail,
        {"emailType": "work", "emailAddress": "anna.work@example.ch"},
        {"emailType": "work", "emailAddress": "anna.other@example.ch"},
    ),
    (
        "addresses",
        ContactAddress,
        {
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        {
            "addressType": "domicile", "addressStreet": "Kramgasse", "addressHouseNumber": "2",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
    ),
    (
        "external-ids",
        ContactExternalId,
        {"systemName": "Salesforce", "externalId": "SF-001"},
        {"systemName": "Salesforce", "externalId": "SF-002"},
    ),
]


def _writer(dealership: dict, token: str, segment: str) -> str:
    """External ids are platform_admin only (a platform-managed CRM/OEM link)."""

    return _admin(dealership) if segment == "external-ids" else token


def test_each_child_create_twice_under_one_key_makes_one_row(client, db_session):
    dealership, token = _setup(client)
    for segment, model, body, _other in _CHILD_CREATES:
        contact = _contact(client, token)
        before = _count(db_session, model, model.contact_id == uuid.UUID(contact["id"]))

        _twice(client, f"/v1/contacts/{contact['id']}/{segment}", _writer(dealership, token, segment), body)

        after = _count(db_session, model, model.contact_id == uuid.UUID(contact["id"]))
        assert after == before + 1, segment


def test_each_child_create_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    dealership, token = _setup(client)
    for segment, model, body, other in _CHILD_CREATES:
        contact = _contact(client, token)
        path = f"/v1/contacts/{contact['id']}/{segment}"
        writer = _writer(dealership, token, segment)
        key, _ = _twice(client, path, writer, body)
        before = _count(db_session, model, model.contact_id == uuid.UUID(contact["id"]))

        response = client.post(path, json=other, headers=_headers(writer, key))

        _assert_key_conflict(response, key)
        assert _count(db_session, model, model.contact_id == uuid.UUID(contact["id"])) == before, segment


# --- POST /v1/contacts/{id}/vehicles ----------------------------------------


def _vehicle(client, token: str) -> dict:
    vin = "".join(random.choices("ABCDEFGHJKLMNPRSTUVWXYZ0123456789", k=17))
    response = client.post("/v1/vehicle-mdm", json={"vin": vin}, headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()["vehicle"]


def test_a_retried_link_replays_instead_of_taking_the_vehicle_back(client, db_session):
    """Re-linking the same contact, vehicle and role is a no-op, so a retry
    straight after the first link would pass without the key. A retry after
    another contact has become the owner shows it: run again, it would close
    B's ownership and make A the owner once more."""

    _, token = _setup(client)
    first_owner = _contact(client, token)
    next_owner = _contact(client, token)
    vehicle = _vehicle(client, token)
    body = {"vehicleId": vehicle["id"], "role": "owner"}
    key = str(uuid.uuid4())
    path = f"/v1/contacts/{first_owner['id']}/vehicles"

    first = client.post(path, json=body, headers=_headers(token, key))
    assert first.status_code == 201, first.text
    taken = client.post(f"/v1/contacts/{next_owner['id']}/vehicles", json=body, headers=_headers(token))
    assert taken.status_code == 201, taken.text
    retry = client.post(path, json=body, headers=_headers(token, key))

    assert retry.status_code == first.status_code
    assert retry.json() == first.json()
    db_session.expire_all()
    open_owners = db_session.scalars(
        select(VehicleParty.contact_id).where(
            VehicleParty.vehicle_id == uuid.UUID(vehicle["id"]),
            VehicleParty.role == VehiclePartyRole.OWNER,
            VehicleParty.effective_to.is_(None),
        )
    ).all()
    assert open_owners == [uuid.UUID(next_owner["id"])]
    assert _count(db_session, VehicleParty, VehicleParty.contact_id == uuid.UUID(first_owner["id"])) == 1


def test_link_vehicle_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    _, token = _setup(client)
    contact = _contact(client, token)
    vehicle = _vehicle(client, token)
    path = f"/v1/contacts/{contact['id']}/vehicles"
    key, _ = _twice(client, path, token, {"vehicleId": vehicle["id"], "role": "owner"})

    response = client.post(path, json={"vehicleId": vehicle["id"], "role": "driver"}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, VehicleParty, VehicleParty.contact_id == uuid.UUID(contact["id"])) == 1


# --- POST /v1/contacts/{id}/legal-basis -------------------------------------


def test_record_legal_basis_twice_under_one_key_makes_one_basis(client, db_session):
    dealership, token = _setup(client)
    contact = _contact(client, token)
    body = {"basis": "joint_controller_agreement", "scope": "contact data", "sourceDocument": "JCA 2026-10-08"}

    _twice(client, f"/v1/contacts/{contact['id']}/legal-basis", _admin(dealership), body)

    assert _count(db_session, LegalBasis, LegalBasis.contact_id == uuid.UUID(contact["id"])) == 1


def test_record_legal_basis_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    dealership, token = _setup(client)
    contact = _contact(client, token)
    path = f"/v1/contacts/{contact['id']}/legal-basis"
    body = {"basis": "joint_controller_agreement", "scope": "contact data", "sourceDocument": "JCA 2026-10-08"}
    key, _ = _twice(client, path, _admin(dealership), body)

    response = client.post(path, json={**body, "scope": "everything"}, headers=_headers(_admin(dealership), key))

    _assert_key_conflict(response, key)
    assert _count(db_session, LegalBasis, LegalBasis.contact_id == uuid.UUID(contact["id"])) == 1


# --- transitions: credit-block and merge (If-Match) --------------------------


def test_a_retried_credit_block_replays_its_success_instead_of_a_version_conflict(client, db_session):
    _, token = _setup(client)
    contact = _contact(client, token)
    path = f"/v1/contacts/{contact['id']}/credit-block"

    _, body = _twice(client, path, token, {"blocked": True, "reason": "Unpaid invoice"}, **{"If-Match": "1"})

    assert body["creditBlock"] is True
    db_session.expire_all()
    assert db_session.get(Contact, uuid.UUID(contact["id"])).version == body["version"] == 2


def test_credit_block_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    _, token = _setup(client)
    contact = _contact(client, token)
    path = f"/v1/contacts/{contact['id']}/credit-block"
    key, _ = _twice(client, path, token, {"blocked": True, "reason": "Unpaid invoice"}, **{"If-Match": "1"})

    response = client.post(path, json={"blocked": False}, headers=_headers(token, key, **{"If-Match": "2"}))

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(Contact, uuid.UUID(contact["id"])).credit_block is True


def test_a_retried_merge_replays_its_success_instead_of_a_version_conflict(client, db_session):
    _, token = _setup(client)
    survivor = _contact(client, token)
    duplicate = _contact(client, token)
    path = f"/v1/contacts/{duplicate['id']}/merge"

    _, body = _twice(client, path, token, {"duplicateOfContactId": survivor["id"]}, **{"If-Match": "1"})

    assert body["duplicateOfContactId"] == survivor["id"]
    db_session.expire_all()
    assert db_session.get(Contact, uuid.UUID(duplicate["id"])).version == body["version"] == 2


def test_merge_with_a_reused_key_and_another_survivor_is_a_409(client, db_session):
    _, token = _setup(client)
    survivor = _contact(client, token)
    other = _contact(client, token)
    duplicate = _contact(client, token)
    path = f"/v1/contacts/{duplicate['id']}/merge"
    key, _ = _twice(client, path, token, {"duplicateOfContactId": survivor["id"]}, **{"If-Match": "1"})

    response = client.post(
        path, json={"duplicateOfContactId": other["id"]}, headers=_headers(token, key, **{"If-Match": "1"})
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(Contact, uuid.UUID(duplicate["id"])).duplicate_of_contact_id == uuid.UUID(survivor["id"])
