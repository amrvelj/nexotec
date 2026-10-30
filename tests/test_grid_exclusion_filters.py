"""KAN-476 — «ist nicht» on the grids' select filters, applied server-side.

Each list endpoint the Stock, Customers and Catalogue grids read takes an
exclusion per select field (an additive, typed parameter: an unknown enum
value is 422). A record with no value for the field is kept — it is not that
value. Over HTTP, on Postgres.
"""

import uuid
from decimal import Decimal

from app.core.auth import AccessRole, create_access_token
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from tests.configuration_test_support import seed_reference_list, stock_configuration_id
from tests.dealership_support import valid_uid
from tests.membership_support import create_user
from tests.test_catalogue_browse import _enable_browse
from tests.token_support import dealer_group_of

ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}


def _dealership(client) -> uuid.UUID:
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset({AccessRole.PLATFORM_ADMIN})
    )
    response = client.post(
        "/v1/dealerships",
        json={"legalName": "Garage Musterbetrieb AG", "franchiseType": "independent", "address": ADDRESS, "phone": "+41441234567", "uid": valid_uid()},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _headers(tenant_id: uuid.UUID, role: AccessRole | None = None, *, manager: bool = False) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({role}) if role else frozenset(), is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


# --- Stock -------------------------------------------------------------------------------------


def test_stock_excludes_a_condition_and_a_lifecycle_status(client, db_session):
    headers = _headers(uuid.uuid4(), AccessRole.INVENTORY)

    def create(**body):
        body = {"vehicleLabel": "VW Golf", "configurationId": stock_configuration_id(db_session, headers), **body}
        response = client.post("/v1/inventory/stock-items", json=body, headers=headers)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    new_pipeline = create(condition="new")
    used_pipeline = create(condition="used")
    used_in_stock = create(condition="used", vin="WVWZZZ1KZAW047601")

    assert _ids(client.get("/v1/inventory/stock-items?condition_not=used", headers=headers)) == {new_pipeline}
    assert _ids(client.get("/v1/inventory/stock-items?lifecycle_status_not=pipeline", headers=headers)) == {used_in_stock}
    both = client.get("/v1/inventory/stock-items?condition=used&lifecycle_status_not=in_stock", headers=headers)
    assert _ids(both) == {used_pipeline}
    assert client.get("/v1/inventory/stock-items?condition_not=occasion", headers=headers).status_code == 422


# --- Customers -----------------------------------------------------------------------------------


def _customer(client, tenant_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": f"Muster {uuid.uuid4().hex[:4]}",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"{uuid.uuid4().hex[:8]}@example.ch"}],
        **overrides,
    }
    payload = {key: value for key, value in payload.items() if value is not None or key == "advisorId"}
    response = client.post("/v1/customers", json=payload, headers=_headers(tenant_id, manager=True))
    assert response.status_code == 201, response.text
    return response.json()


def _domicile(postal_code: str, locality: str) -> list[dict]:
    return [
        {
            "addressType": "domicile",
            "addressStreet": "Hauptstrasse",
            "addressHouseNumber": "1",
            "addressPostalCode": postal_code,
            "addressLocality": locality,
            "addressCountry": "CH",
        }
    ]


def test_customers_exclude_a_type_a_language_and_a_lifecycle_status(client):
    tenant = _dealership(client)
    headers = _headers(tenant, manager=True)
    anna = _customer(client, tenant, language="de")
    marie = _customer(client, tenant, language="fr")
    firma = _customer(
        client, tenant, customerType="business", companyName="Garage Beispiel AG", language="it", firstName=None, lastName=None
    )
    activated = client.patch(
        f"/v1/customers/{marie['id']}", json={"lifecycleStatus": "active"}, headers={**headers, "If-Match": "1"}
    )
    assert activated.status_code == 200, activated.text

    assert _ids(client.get("/v1/customers?customer_type_not=business", headers=headers)) == {anna["id"], marie["id"]}
    assert _ids(client.get("/v1/customers?language_not=de", headers=headers)) == {marie["id"], firma["id"]}
    assert _ids(client.get("/v1/customers?lifecycle_status_not=active", headers=headers)) == {anna["id"], firma["id"]}
    assert client.get("/v1/customers?language_not=es", headers=headers).status_code == 422


def test_customers_exclude_a_canton_keeping_those_without_a_domicile(client):
    tenant = _dealership(client)
    headers = _headers(tenant, manager=True)
    zurich = _customer(client, tenant, addresses=_domicile("8001", "Zürich"))
    bern = _customer(client, tenant, addresses=_domicile("3011", "Bern"))
    nowhere = _customer(client, tenant)

    assert _ids(client.get("/v1/customers?canton=ZH", headers=headers)) == {zurich["id"]}
    assert _ids(client.get("/v1/customers?canton_not=ZH", headers=headers)) == {bern["id"], nowhere["id"]}


def test_customers_exclude_an_advisor_keeping_the_unassigned_and_assigned_keeps_only_those_with_one(client):
    tenant = _dealership(client)
    headers = _headers(tenant, manager=True)
    beat = create_user(client, str(tenant), is_dealer_manager=False)["id"]
    carla = create_user(client, str(tenant), is_dealer_manager=False)["id"]
    unassigned = _customer(client, tenant, advisorId=None)
    of_beat = _customer(client, tenant, advisorId=beat)
    of_carla = _customer(client, tenant, advisorId=carla)
    assert (unassigned["advisorId"], of_beat["advisorId"], of_carla["advisorId"]) == (None, beat, carla)

    not_beat = _ids(client.get(f"/v1/customers?advisorUserIdNot={beat}", headers=headers))
    assert not_beat == {unassigned["id"], of_carla["id"]}
    assert _ids(client.get("/v1/customers?assigned=true", headers=headers)) == {of_beat["id"], of_carla["id"]}


# --- Catalogue -----------------------------------------------------------------------------------


def test_catalogue_variants_exclude_a_coded_value_keeping_those_without_one(client, db_session):
    tenant = uuid.uuid4()
    _enable_browse(db_session, tenant)
    brand = Brand(code="vw", display_name="Volkswagen")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db_session.add(group)
    db_session.flush()
    for name, fuel in (("Golf GTI", "petrol"), ("Golf TDI", "diesel"), ("Golf (unbekannt)", None)):
        db_session.add(
            ModelVariant(
                model_group_id=group.id, name=name, model_year_from=2022, vehicle_kind="passenger_car", fuel_type=fuel,
                base_price=Decimal("40000.00"),
            )
        )
    db_session.commit()

    response = client.get("/v1/catalogue/variants?mode=record&fuelTypeNot=petrol", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    assert {v["variantName"] for v in response.json()["items"]} == {"Golf TDI", "Golf (unbekannt)"}


# --- Typed: an unknown value is 422, never a filter that excludes nothing ---------------------


def test_an_unknown_canton_or_catalogue_code_to_exclude_is_422(client, db_session):
    tenant = _dealership(client)
    assert client.get("/v1/customers?canton_not=XX", headers=_headers(tenant, manager=True)).status_code == 422

    _enable_browse(db_session, tenant)
    response = client.get("/v1/catalogue/variants?mode=record&fuelTypeNot=nonsense", headers=_headers(tenant))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"invalid": {"fuelTypeNot": "nonsense"}}
    # An active value of the list is accepted even where no variant carries it.
    seed_reference_list(db_session, "fuel_type", "petrol", "diesel")
    db_session.commit()
    assert client.get("/v1/catalogue/variants?mode=record&fuelTypeNot=diesel", headers=_headers(tenant)).status_code == 200
