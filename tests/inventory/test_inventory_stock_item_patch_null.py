"""KAN-441 — `PATCH /v1/inventory/stock-items/{id}` with an explicit
`null` for a NOT NULL column (`condition`, `vehicleLabel`) is a 422 naming
the field, as KAN-394 made it for the customer; before, the null reached
the flush and the NotNullViolation was a 500. An absent key still leaves the
value alone."""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item


def _item_and_headers(db_session) -> tuple[StockItem, dict[str, str]]:
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False,
    )
    return item, {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("field", ["condition", "vehicleLabel"])
def test_an_explicit_null_for_a_not_null_field_is_422_naming_it(client, db_session, field):
    item, headers = _item_and_headers(db_session)

    response = client.patch(
        f"/v1/inventory/stock-items/{item.id}", json={field: None}, headers={**headers, "If-Match": str(item.version)}
    )

    assert response.status_code == 422, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert [e["loc"] for e in errors] == [["body", field]]
    db_session.expire_all()
    stored = db_session.get(StockItem, item.id)
    assert (stored.vehicle_label, stored.condition, stored.version) == (
        "Škoda Octavia", StockItemCondition.USED, item.version
    )


def test_an_absent_key_leaves_the_value_alone(client, db_session):
    item, headers = _item_and_headers(db_session)

    response = client.patch(
        f"/v1/inventory/stock-items/{item.id}", json={"odometerKm": 12000}, headers={**headers, "If-Match": str(item.version)}
    )

    assert response.status_code == 200, response.text
    assert (response.json()["vehicleLabel"], response.json()["condition"]) == ("Škoda Octavia", "used")


def test_a_nullable_field_still_clears_with_null(client, db_session):
    item, headers = _item_and_headers(db_session)

    response = client.patch(
        f"/v1/inventory/stock-items/{item.id}", json={"exteriorColour": None}, headers={**headers, "If-Match": str(item.version)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["exteriorColour"] is None
