"""WP-5 PR-9: VehicleMdm API — identity editing, FR-V-15's VIN-exists
behavior, and the one search box.
"""

import uuid

from app.core.auth import AccessRole, create_access_token

VALID_VIN = "1HGCM82633A004352"


def _token(role: AccessRole | None = None, is_dealer_manager: bool = False) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_returns_created_true_on_a_new_vin(client):
    token = _token(is_dealer_manager=True)
    response = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["vehicle"]["vin"] == VALID_VIN


def test_duplicate_vin_is_not_an_error_offers_the_existing_record(client):
    token = _token(is_dealer_manager=True)
    first = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()

    second = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token))
    assert second.status_code == 200, second.text  # never 409, never 422
    body = second.json()
    assert body["created"] is False
    assert body["vehicle"]["id"] == first["vehicle"]["id"]


def test_patch_identity_fields_requires_if_match(client):
    token = _token(is_dealer_manager=True)
    created = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()["vehicle"]

    response = client.patch(
        f"/v1/vehicle-mdm/{created['id']}", json={"stammnummer": "123456789"},
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stammnummer"] == "123456789"


def test_search_by_exact_vin_resolves_above_the_grid(client):
    token = _token(is_dealer_manager=True)
    created = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()["vehicle"]

    response = client.get(f"/v1/vehicle-mdm/search?q={VALID_VIN}", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"]["id"] == created["id"]
    assert body["pickerCandidates"] == []


def test_search_by_free_text_filters_never_resolves(client):
    token = _token(is_dealer_manager=True)
    client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token))

    response = client.get("/v1/vehicle-mdm/search?q=some-brand-fragment", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"] is None
    assert body["pickerCandidates"] == []
    assert "items" in body["filtered"]


def test_search_unresolvable_vin_shaped_string_resolves_to_nothing_not_a_filter(client):
    token = _token(is_dealer_manager=True)
    response = client.get("/v1/vehicle-mdm/search?q=ZZZZZZZZZZZZZZZZZ", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"] is None
    assert body["pickerCandidates"] == []


def _token_with_group(group_id: uuid.UUID, is_dealer_manager: bool = True) -> str:
    tid = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=group_id,
        roles=frozenset(), is_dealer_manager=is_dealer_manager,
    )


def test_allocate_to_customer_via_vehicle_side(client, db_session):
    group_id = uuid.uuid4()
    token = _token_with_group(group_id)
    created = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token)).json()["vehicle"]

    from app.customer.models.customer import Customer, CustomerType, Language

    customer = Customer(
        group_id=group_id, customer_number="K-000001", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace",
    )
    db_session.add(customer)
    db_session.commit()

    response = client.post(
        f"/v1/vehicle-mdm/{created['id']}/allocate",
        json={"customerId": str(customer.id), "role": "owner"},
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["customerId"] == str(customer.id)
    assert response.json()["displayName"] == "Ada Lovelace"  # KAN-140


def test_allocate_to_customer_from_another_group_404s_not_403(client, db_session):
    """VehicleParty carries no group_id column and vehicle_mdm is a
    deliberately global fact (ADR-022), so nothing but this endpoint's own
    lookup stops a principal from group A naming a customer_id that
    belongs to group B. Confirming a real cross-group allocation would be
    a silent write-side PII/authorization leak — the customer-side dialog
    already prevents this by resolving `customer` through
    get_customer_or_404(principal.group_id, ...) before it ever calls
    allocate_vehicle_party; this route must reject the same way, per rule
    #7 (a cross-group reference looks like the record doesn't exist, not
    a 403).
    """

    group_a = uuid.uuid4()
    group_b = uuid.uuid4()
    token_a = _token_with_group(group_a)
    created = client.post("/v1/vehicle-mdm", json={"vin": VALID_VIN}, headers=_bearer(token_a)).json()["vehicle"]

    from app.customer.models.customer import Customer, CustomerType, Language

    customer_b = Customer(
        group_id=group_b, customer_number="K-000002", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Bob", last_name="OtherGroup",
    )
    db_session.add(customer_b)
    db_session.commit()

    response = client.post(
        f"/v1/vehicle-mdm/{created['id']}/allocate",
        json={"customerId": str(customer_b.id), "role": "owner"},
        headers=_bearer(token_a),
    )
    assert response.status_code == 404, response.text

    # Unfiltered on purpose: a rogue row would belong to group B, so a
    # group-A-scoped read (list_vehicle_parties) would pass vacuously.
    from sqlalchemy import select

    from app.customer.models.vehicle_party import VehicleParty

    rows = db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == uuid.UUID(created["id"]))).all()
    assert rows == []
