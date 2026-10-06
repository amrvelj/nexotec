"""KAN-152 item 3: GET /v1/inventory/stock-items/{id}/equipment declares
its response shape, so schema.d.ts generates the type the publishing tab
reads instead of the tab hand-writing one (KAN-35)."""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.stock_item import get_stock_item_or_404
from app.vehicle.public import get_vehicle_mdm_or_404

_EQUIPMENT_PATH = "/v1/inventory/stock-items/{stock_item_id}/equipment"


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.INVENTORY}),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_equipment_endpoint_declares_its_response_schema(client):
    openapi = client.get("/openapi.json").json()
    response = openapi["paths"][_EQUIPMENT_PATH]["get"]["responses"]["200"]
    ref = response["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/EquipmentRead"

    schema = openapi["components"]["schemas"]["EquipmentRead"]
    assert set(schema["properties"]) == {"ausstattungCodes", "extras", "eigenschaften", "providerAusstattung"}
    assert set(schema["required"]) == {"ausstattungCodes", "extras", "eigenschaften", "providerAusstattung"}


def test_equipment_of_a_pipeline_item_is_empty(client):
    token = _token(uuid.uuid4())
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Factory order", "condition": "new"},
        headers=_bearer(token),
    ).json()

    response = client.get(f"/v1/inventory/stock-items/{created['id']}/equipment", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"ausstattungCodes": [], "extras": [], "eigenschaften": [], "providerAusstattung": {}}


def test_equipment_of_a_promoted_item_comes_from_the_vehicle(client, db_session):
    tenant_id = uuid.uuid4()
    token = _token(tenant_id)
    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used"},
        headers=_bearer(token),
    ).json()
    item = get_stock_item_or_404(db_session, tenant_id, uuid.UUID(created["id"]))
    item = promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")
    assert item.vehicle_id is not None
    vehicle = get_vehicle_mdm_or_404(db_session, item.vehicle_id)
    vehicle.ausstattung_codes = ["A1"]
    vehicle.extras = ["Anhängerkupplung"]
    vehicle.eigenschaften = ["Nichtraucher"]
    vehicle.provider_ausstattung = {"de": "Navigationssystem"}
    db_session.commit()

    response = client.get(f"/v1/inventory/stock-items/{created['id']}/equipment", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ausstattungCodes": ["A1"],
        "extras": ["Anhängerkupplung"],
        "eigenschaften": ["Nichtraucher"],
        "providerAusstattung": {"de": "Navigationssystem"},
    }
