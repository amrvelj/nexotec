import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.models.reference_data import ReferenceList, ReferenceValue

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}
VALID_VIN = "1HGCM82633A004352"
VALID_VIN_2 = "2HGCM82633A004353"


def _token(role: AccessRole, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> str:
    return create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id or uuid.uuid4(), access_role=role
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
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _vehicle_payload(**overrides):
    payload = {"vin": VALID_VIN, "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    return payload


def _create_vehicle(client, dealer_id: str, **overrides):
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/vehicles", json=_vehicle_payload(**overrides), headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _seed_list(db_session, list_code: str) -> ReferenceList:
    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.commit()
    db_session.refresh(ref_list)
    return ref_list


def _seed_value(db_session, ref_list: ReferenceList, value_code: str) -> ReferenceValue:
    value = ReferenceValue(
        list_id=ref_list.id, value_code=value_code, label_de=value_code, label_fr=value_code, label_it=value_code
    )
    db_session.add(value)
    db_session.commit()
    db_session.refresh(value)
    return value


# --- creation / access control ------------------------------------------------


def test_dealer_admin_can_create_vehicle_and_becomes_custodian(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    assert body["vin"] == VALID_VIN
    assert body["status"] == "in_transit"
    assert body["currentCustodianPartnerId"] == dealer_id
    assert body["version"] == 1


def test_inventory_can_create_vehicle(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.INVENTORY, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/vehicles", json=_vehicle_payload(), headers=_bearer(token))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.AUDITOR])
def test_non_write_roles_cannot_create_vehicle(client, role):
    dealer_id = _create_dealer(client)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/vehicles", json=_vehicle_payload(), headers=_bearer(token))
    assert response.status_code == 403


def test_create_vehicle_requires_authentication(client):
    response = client.post("/v1/vehicles", json=_vehicle_payload())
    assert response.status_code == 401


def test_creates_initial_acquired_custody_event(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    events = client.get(f"/v1/vehicles/{body['id']}/custody-events", headers=_bearer(token))
    assert events.status_code == 200
    items = events.json()["items"]
    assert len(items) == 1
    assert items[0]["eventType"] == "acquired"
    assert items[0]["partnerId"] == dealer_id


# --- VIN validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_vin",
    [
        "1hgcm82633a004352",  # lowercase
        "1HGCM82633A00435",  # 16 chars
        "1HGCM82633A0043522",  # 18 chars
        "1HGCM8263 A004352",  # whitespace
        "1HGCM82633AOO4352",  # contains O
        "1HGCM82633AI04352",  # contains I
    ],
)
def test_malformed_vin_is_rejected_not_normalized(client, bad_vin):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post("/v1/vehicles", json=_vehicle_payload(vin=bad_vin), headers=_bearer(token))
    assert response.status_code == 422


def test_duplicate_vin_is_rejected(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    _create_vehicle(client, dealer_a)

    token_b = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))
    response = client.post("/v1/vehicles", json=_vehicle_payload(), headers=_bearer(token_b))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_model_year_out_of_range_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/vehicles", json=_vehicle_payload(modelYear=1975), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_cannot_create_vehicle_already_totaled(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/vehicles", json=_vehicle_payload(status="totaled"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_condition_is_required(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = _vehicle_payload()
    del payload["condition"]
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 422


def test_invalid_condition_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/vehicles", json=_vehicle_payload(condition="mint"), headers=_bearer(token)
    )
    assert response.status_code == 422


def test_condition_round_trips(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id, condition="certified_pre_owned")
    assert body["condition"] == "certified_pre_owned"


# --- reference-data field validation ----------------------------------------------


def test_unknown_reference_value_code_is_rejected(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        "/v1/vehicles", json=_vehicle_payload(fuelType="not_a_real_value"), headers=_bearer(token)
    )
    assert response.status_code == 404


def test_valid_reference_value_code_is_accepted(client, db_session):
    ref_list = _seed_list(db_session, "fuel_type")
    _seed_value(db_session, ref_list, "diesel")

    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id, fuelType="diesel")
    assert body["fuelType"] == "diesel"


# --- visibility rule: status/custodian redaction --------------------------------


def test_static_profile_visible_cross_tenant(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant_id
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(other_token))
    assert response.status_code == 200
    assert response.json()["vin"] == VALID_VIN
    assert response.json()["make"] == "Honda"


def test_status_and_custodian_redacted_for_non_custodian(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(other_token))
    assert response.json()["status"] is None
    assert response.json()["currentCustodianPartnerId"] is None


def test_status_and_custodian_visible_to_current_custodian(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    own_token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(own_token))
    assert response.json()["status"] == "in_transit"
    assert response.json()["currentCustodianPartnerId"] == dealer_id


def test_status_and_custodian_visible_to_platform_admin(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(admin_token))
    assert response.json()["status"] == "in_transit"
    assert response.json()["currentCustodianPartnerId"] == dealer_id


def test_status_visible_to_dealer_with_custody_history_after_losing_custody(client):
    """R1 (PM/CTO ruling, 2026-08-06): a dealer who transferred a vehicle
    away can still see its current `status` (they have custody history),
    even though `currentCustodianPartnerId` correctly redacts to null for
    them (they're no longer the current custodian, and that field's
    visibility rule is unchanged — current custodian or platform_admin
    only, to never leak who holds it now after a transfer).
    """

    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    body = _create_vehicle(client, dealer_a)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(admin_token),
    )

    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(token_a))
    assert response.json()["status"] == "in_transit"
    assert response.json()["currentCustodianPartnerId"] is None


def test_dealer_with_no_custody_history_stays_fully_redacted(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    stranger_token = _token(AccessRole.DEALER_ADMIN)  # different, random tenant, never touched this vehicle
    response = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(stranger_token))
    assert response.json()["status"] is None
    assert response.json()["currentCustodianPartnerId"] is None


def test_get_by_vin_applies_same_redaction(client):
    dealer_id = _create_dealer(client)
    _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)
    response = client.get(f"/v1/vehicles/by-vin/{VALID_VIN}", headers=_bearer(other_token))
    assert response.status_code == 200
    assert response.json()["status"] is None


def test_get_unknown_vin_is_404(client):
    token = _token(AccessRole.DEALER_ADMIN)
    response = client.get("/v1/vehicles/by-vin/9HGCM82633A004399", headers=_bearer(token))
    assert response.status_code == 404


def test_get_unknown_vehicle_id_is_404(client):
    token = _token(AccessRole.DEALER_ADMIN)
    response = client.get(f"/v1/vehicles/{uuid.uuid4()}", headers=_bearer(token))
    assert response.status_code == 404


def test_list_vehicles_visible_to_any_authenticated_role(client):
    dealer_id = _create_dealer(client)
    _create_vehicle(client, dealer_id)

    sales_token = _token(AccessRole.SALES)
    response = client.get("/v1/vehicles", headers=_bearer(sales_token))
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["status"] is None


def test_list_vehicles_requires_authentication(client):
    response = client.get("/v1/vehicles")
    assert response.status_code == 401


# --- optimistic concurrency / PATCH -----------------------------------------------


def test_patch_without_if_match_is_400(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    response = client.patch(f"/v1/vehicles/{body['id']}", json={"trim": "Sport"}, headers=_bearer(token))
    assert response.status_code == 400


def test_patch_with_stale_if_match_is_409(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = {**_bearer(token), "If-Match": "1"}

    first = client.patch(f"/v1/vehicles/{body['id']}", json={"trim": "Sport"}, headers=headers)
    assert first.status_code == 200

    stale = client.patch(f"/v1/vehicles/{body['id']}", json={"trim": "EX-L"}, headers=headers)
    assert stale.status_code == 409


def test_any_dealer_admin_can_patch_spec_fields(client):
    """Static-spec corrections aren't custodian-gated — any dealer_admin/
    inventory user can fix a shared-catalog typo."""

    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)
    response = client.patch(
        f"/v1/vehicles/{body['id']}", json={"trim": "Sport"}, headers={**_bearer(other_token), "If-Match": "1"}
    )
    assert response.status_code == 200
    assert response.json()["trim"] == "Sport"


def test_only_custodian_can_change_status(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)
    response = client.patch(
        f"/v1/vehicles/{body['id']}",
        json={"status": "in_stock"},
        headers={**_bearer(other_token), "If-Match": "1"},
    )
    assert response.status_code == 403


def test_custodian_can_change_status(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    response = client.patch(
        f"/v1/vehicles/{body['id']}", json={"status": "in_stock"}, headers={**_bearer(token), "If-Match": "1"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "in_stock"


def test_platform_admin_can_change_status_for_any_vehicle(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    admin_token = _token(AccessRole.PLATFORM_ADMIN)

    response = client.patch(
        f"/v1/vehicles/{body['id']}", json={"status": "in_stock"}, headers={**_bearer(admin_token), "If-Match": "1"}
    )
    assert response.status_code == 200


def test_terminal_status_cannot_be_changed(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    headers = _bearer(token)

    r1 = client.patch(f"/v1/vehicles/{body['id']}", json={"status": "totaled"}, headers={**headers, "If-Match": "1"})
    assert r1.status_code == 200

    r2 = client.patch(f"/v1/vehicles/{body['id']}", json={"status": "in_stock"}, headers={**headers, "If-Match": "2"})
    assert r2.status_code == 409


# --- custody events -----------------------------------------------------------------


def test_current_custodian_can_transfer_to_another_dealer_as_platform_admin(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    body = _create_vehicle(client, dealer_a)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["partnerId"] == dealer_b

    get_resp = client.get(
        f"/v1/vehicles/{body['id']}", headers=_bearer(_token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b)))
    )
    assert get_resp.json()["currentCustodianPartnerId"] == dealer_b


def test_dealer_cannot_claim_custody_on_behalf_of_another_dealer(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    body = _create_vehicle(client, dealer_a)

    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(token_a),
    )
    assert response.status_code == 403


def test_only_current_custodian_can_record_sold(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)

    other_token = _token(AccessRole.DEALER_ADMIN)
    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "sold"},
        headers=_bearer(other_token),
    )
    assert response.status_code == 403


def test_sold_clears_current_custodian(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events", json={"eventType": "sold"}, headers=_bearer(token)
    )
    assert response.status_code == 201

    get_resp = client.get(f"/v1/vehicles/{body['id']}", headers=_bearer(token))
    assert get_resp.json()["currentCustodianPartnerId"] is None


@pytest.mark.parametrize("status", ["sold", "totaled", "scrapped"])
def test_cannot_record_custody_event_on_terminal_vehicle(client, status):
    """Fix per CTO review, 2026-08-06: a sold/totaled/scrapped vehicle must
    not accept new custody events — otherwise the custody chain silently
    diverges from the terminal `status` field. Uses PATCH (custodian-gated)
    to reach the terminal status directly, independent of Transaction.
    """

    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.patch(
        f"/v1/vehicles/{body['id']}", json={"status": status}, headers={**_bearer(token), "If-Match": "1"}
    )

    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events", json={"eventType": "acquired"}, headers=_bearer(token)
    )
    assert response.status_code == 409


def test_custody_events_are_row_filtered_to_requesters_own_tenant(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    body = _create_vehicle(client, dealer_a)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(admin_token),
    )

    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    events_a = client.get(f"/v1/vehicles/{body['id']}/custody-events", headers=_bearer(token_a))
    assert len(events_a.json()["items"]) == 1
    assert events_a.json()["items"][0]["partnerId"] == dealer_a

    token_b = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))
    events_b = client.get(f"/v1/vehicles/{body['id']}/custody-events", headers=_bearer(token_b))
    assert len(events_b.json()["items"]) == 1
    assert events_b.json()["items"][0]["partnerId"] == dealer_b

    events_admin = client.get(f"/v1/vehicles/{body['id']}/custody-events", headers=_bearer(admin_token))
    assert len(events_admin.json()["items"]) == 2


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.AUDITOR])
def test_non_write_roles_cannot_record_custody_event(client, role):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(role, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/vehicles/{body['id']}/custody-events", json={"eventType": "sold"}, headers=_bearer(token)
    )
    assert response.status_code == 403


# --- audit log (row-filtered like custody-events) -----------------------------


def test_audit_log_records_create(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))

    log = client.get(f"/v1/vehicles/{body['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200
    actions = [item["action"] for item in log.json()["items"]]
    assert "create" in actions


def test_audit_log_records_update(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    client.patch(
        f"/v1/vehicles/{body['id']}", json={"odometer": 12000}, headers={**_bearer(token), "If-Match": "1"}
    )

    log = client.get(f"/v1/vehicles/{body['id']}/audit-log", headers=_bearer(token))
    update_event = next(item for item in log.json()["items"] if item["action"] == "update")
    assert update_event["after"]["odometer"] == 12000


def test_audit_log_is_row_filtered_to_requesters_own_tenant(client):
    dealer_a = _create_dealer(client)
    dealer_b = _create_dealer(client)
    body = _create_vehicle(client, dealer_a)

    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    client.post(
        f"/v1/vehicles/{body['id']}/custody-events",
        json={"eventType": "transferred", "partnerId": dealer_b},
        headers=_bearer(admin_token),
    )

    token_a = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_a))
    log_a = client.get(f"/v1/vehicles/{body['id']}/audit-log", headers=_bearer(token_a))
    actions_a = [item["action"] for item in log_a.json()["items"]]
    assert "create" in actions_a
    assert "custody_event" not in actions_a

    token_b = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_b))
    log_b = client.get(f"/v1/vehicles/{body['id']}/audit-log", headers=_bearer(token_b))
    actions_b = [item["action"] for item in log_b.json()["items"]]
    assert "create" not in actions_b
    assert "custody_event" in actions_b

    log_admin = client.get(f"/v1/vehicles/{body['id']}/audit-log", headers=_bearer(admin_token))
    assert len(log_admin.json()["items"]) == 2


def test_audit_log_requires_authentication(client):
    dealer_id = _create_dealer(client)
    body = _create_vehicle(client, dealer_id)
    response = client.get(f"/v1/vehicles/{body['id']}/audit-log")
    assert response.status_code == 401


# --- pagination -----------------------------------------------------------------


def test_list_vehicles_paginates(client):
    dealer_id = _create_dealer(client)
    _create_vehicle(client, dealer_id, vin=VALID_VIN)
    _create_vehicle(client, dealer_id, vin=VALID_VIN_2)

    token = _token(AccessRole.DEALER_ADMIN, tenant_id=uuid.UUID(dealer_id))
    first_page = client.get("/v1/vehicles?limit=1", headers=_bearer(token))
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["nextCursor"] is not None

    second_page = client.get(
        f"/v1/vehicles?limit=1&cursor={first_page.json()['nextCursor']}", headers=_bearer(token)
    )
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["nextCursor"] is None
