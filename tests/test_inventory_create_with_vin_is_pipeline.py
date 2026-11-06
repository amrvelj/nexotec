"""KAN-554 (ADR-116; Anto's ruling on KAN-529, 2026-11-03, and on KAN-554,
2026-11-05, option A): a stock create with a VIN lands as a pipeline item
carrying that VIN; only purchase booking puts it in stock (ADR-103).

Before KAN-554, `POST /v1/inventory/stock-items` with a `vin` created the
item `in_stock` at once, with a vehicle-mdm record — a car on stock that
nobody had bought. Now the create stores the VIN (normalised and validated,
KAN-244), checks it against the configuration's (KAN-463) and against the
VINs this Betrieb already holds (KAN-111), and writes nothing to
vehicle-mdm: a car not yet bought has no record there (ADR-045). Booking its
purchase promotes it (FR-V-04) under the VIN it carries. Over HTTP, on
Postgres.
"""

import uuid

from sqlalchemy import func, select

from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.configuration_test_support import stock_configuration_id
from tests.test_inventory_purchase_configuration_vin import _configuration
from tests.test_inventory_purchase_promotes import _auth, _events, _purchase, _token

VIN = "WVWZZZ1KZAW005541"
OTHER_VIN = "WVWZZZ1KZAW005542"


def _create(client, db_session, token: str, **body) -> dict:
    response = _post(client, db_session, token, **body)
    assert response.status_code == 201, response.text
    return response.json()


def _post(client, db_session, token: str, **body):
    body = {"condition": "used", "configurationId": stock_configuration_id(db_session, token), **body}
    return client.post("/v1/inventory/stock-items", json=body, headers=_auth(token))


def _vehicle_mdm_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(VehicleMdm))


def _item_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(StockItem))


def test_a_create_with_a_vin_is_a_pipeline_item_carrying_the_vin(client, db_session):
    token = _token(client)

    created = _create(client, db_session, token, vin=" wvwzzz1kzaw005541 ")

    assert created["lifecycleStatus"] == "pipeline"
    assert created["vin"] == VIN
    assert created["inStockAt"] is None
    assert created["vehicleId"] is None
    assert created["isInvoiceable"] is False
    assert _vehicle_mdm_count(db_session) == 0
    assert _events(db_session, created["id"]) == ["inventory.stock_item.added"]


def test_booking_its_purchase_puts_it_in_stock_under_the_vin_it_carries(client, db_session):
    token = _token(client)
    created = _create(client, db_session, token, vin=VIN)

    booked = _purchase(client, token, created)  # no `vin`: the item's own is used

    assert booked.status_code == 200, booked.text
    body = booked.json()
    assert body["lifecycleStatus"] == "in_stock"
    assert body["inStockAt"] is not None
    assert body["vin"] == VIN
    assert body["isInvoiceable"] is True
    vehicle = db_session.scalar(select(VehicleMdm).where(VehicleMdm.vin == VIN))
    assert vehicle is not None
    assert body["vehicleId"] == str(vehicle.id)
    assert _events(db_session, created["id"]) == [
        "inventory.stock_item.added",
        "inventory.pipeline_vehicle.vin_assigned",
        "inventory.stock_item.purchased",
    ]


def test_its_purchase_may_repeat_the_vin_in_another_spelling(client, db_session):
    token = _token(client)
    created = _create(client, db_session, token, vin=VIN)

    booked = _purchase(client, token, created, vin=VIN.lower())

    assert booked.status_code == 200, booked.text
    assert booked.json()["lifecycleStatus"] == "in_stock"


def test_its_purchase_under_another_vin_is_refused_and_writes_nothing(client, db_session):
    token = _token(client)
    created = _create(client, db_session, token, vin=VIN)

    refused = _purchase(client, token, created, vin=OTHER_VIN)

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "purchase_vin_mismatch", "stockItemId": created["id"]}
    item = client.get(f"/v1/inventory/stock-items/{created['id']}", headers=_auth(token)).json()
    assert item["lifecycleStatus"] == "pipeline"
    assert item["vin"] == VIN
    assert item["purchasePrice"] is None
    assert _vehicle_mdm_count(db_session) == 0
    assert _events(db_session, created["id"]) == ["inventory.stock_item.added"]


def test_a_create_with_a_vin_already_in_stock_is_409(client, db_session):
    token = _token(client)
    held = _create(client, db_session, token, vin=VIN)
    assert _purchase(client, token, held).status_code == 200

    refused = _post(client, db_session, token, vin=VIN)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == {
        "reason": "vin_already_in_stock", "stockItemId": held["id"], "stockNumber": held["stockNumber"],
    }
    assert _item_count(db_session) == 1


def test_a_create_with_a_vin_another_pipeline_item_carries_is_409(client, db_session):
    """The VIN is held from the create on: `uq_stock_item_tenant_id_vin_in_stock`
    covers every item that has not left stock, a pipeline one included."""

    token = _token(client)
    held = _create(client, db_session, token, vin=VIN)

    refused = _post(client, db_session, token, vin=VIN)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "vin_already_in_stock"
    assert refused.json()["error"]["details"]["stockItemId"] == held["id"]


def test_a_create_with_a_vin_that_differs_from_the_configurations_is_422(client, db_session):
    token = _token(client)
    configuration_id = _configuration(db_session, token, vin=VIN)

    refused = client.post(
        "/v1/inventory/stock-items",
        json={"condition": "used", "configurationId": configuration_id, "vin": OTHER_VIN},
        headers=_auth(token),
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == "configuration_vin_differs"
    assert _item_count(db_session) == 0


def test_a_pre_1981_chassis_number_is_still_refused_on_create(client, db_session):
    """KAN-535 is open: the create still takes a strict 17-character VIN."""

    token = _token(client)

    refused = _post(client, db_session, token, vin="1302412345")

    assert refused.status_code == 422, refused.text
    assert [e["loc"] for e in refused.json()["error"]["details"]["errors"]] == [["body", "vin"]]
    assert _item_count(db_session) == 0


def test_the_openapi_says_a_create_lands_in_pipeline(client):
    spec = client.get("/openapi.json").json()
    description = spec["paths"]["/v1/inventory/stock-items"]["post"]["description"]
    vin = spec["components"]["schemas"]["StockItemCreateRequest"]["properties"]["vin"]["description"]

    assert "`lifecycleStatus: pipeline`" in description
    assert "only path to `in_stock`" in description
    assert "taken straight into stock" not in vin
    assert "pipeline" in vin


def test_no_inventory_event_announces_a_car_in_stock_before_its_purchase(client, db_session):
    token = _token(client)
    created = _create(client, db_session, token, vin=VIN)

    vin_assigned = db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.pipeline_vehicle.vin_assigned",
            OutboxMessage.aggregate_id == uuid.UUID(created["id"]),
        )
    )
    assert vin_assigned == 0
