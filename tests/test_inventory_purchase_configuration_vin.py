"""KAN-510 (Anto, 2026-11-01: option B, then A): purchase booking puts a
pipeline car on stock with the VIN it is given (ADR-103). Since KAN-463 the
car's configuration is its technical data, so a VIN that is not the
configuration's would make the stock car and its configuration describe two
cars. The booking refuses it — 422 `configuration_vin_differs`, the refusal
the stock create and «Konfiguration zuordnen» already use — before anything
is read from or written to vehicle-mdm or the item. A configuration without
a VIN (a factory order, `build`) takes the typed VIN as before, and an item
without a configuration is booked as before. Over HTTP, on Postgres.
"""

import uuid

from sqlalchemy import func, select

from app.core.auth import decode_token_claims
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item
from app.vehicle.models.configuration import ConfigurationMode
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services import configuration as configuration_service
from tests.configuration_test_support import stock_configuration_id
from tests.test_inventory_purchase_promotes import _auth, _events, _purchase, _token

CONFIGURED_VIN = "WVWZZZ1KZAW005101"
OTHER_VIN = "WVWZZZ1KZAW005102"


def _tenant_of(token: str) -> uuid.UUID:
    return uuid.UUID(decode_token_claims(token)["tenant_id"])


def _configuration(db_session, token: str, *, vin: str | None, mode: ConfigurationMode = ConfigurationMode.RECORD) -> str:
    """A configuration the configurator produced, carrying `vin` (as its
    VIN field stores it: normalised) or none."""

    configuration_id = stock_configuration_id(db_session, token, mode=mode)
    if vin is not None:
        configuration = configuration_service.get_configuration_or_404(
            db_session, tenant_id=_tenant_of(token), configuration_id=uuid.UUID(configuration_id)
        )
        configuration.vin = vin
        db_session.commit()
    return configuration_id


def _pipeline_item(client, token: str, configuration_id: str) -> dict:
    """The Stock list's intake: a pipeline item with its configuration, no
    VIN (the intake never sends one, `AddToPipelineButton.tsx`)."""

    response = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "VW Golf 8", "condition": "used", "configurationId": configuration_id},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lifecycleStatus"] == "pipeline"
    assert body["vin"] is None
    return body


def _vehicle_mdm_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(VehicleMdm))


def test_the_configurations_vin_books_the_purchase(client, db_session):
    token = _token(client)
    item = _pipeline_item(client, token, _configuration(db_session, token, vin=CONFIGURED_VIN))

    response = _purchase(client, token, item, vin=CONFIGURED_VIN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lifecycleStatus"] == "in_stock"
    assert body["vin"] == CONFIGURED_VIN
    assert body["isInvoiceable"] is True


def test_the_configurations_vin_typed_in_another_spelling_books_the_purchase(client, db_session):
    """Lower case and in groups is the same VIN (KAN-244's one spelling)."""

    token = _token(client)
    item = _pipeline_item(client, token, _configuration(db_session, token, vin=CONFIGURED_VIN))

    response = _purchase(client, token, item, vin=" wvwzzz1kzaw 005101 ")

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == CONFIGURED_VIN


def test_a_vin_that_is_not_the_configurations_is_refused_and_nothing_is_written(client, db_session):
    token = _token(client)
    item = _pipeline_item(client, token, _configuration(db_session, token, vin=CONFIGURED_VIN))
    vehicles_before = _vehicle_mdm_count(db_session)

    response = _purchase(client, token, item, vin=OTHER_VIN)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "vin", "reason": "configuration_vin_differs"}
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert stored.lifecycle_status.value == "pipeline"
    assert stored.vin is None
    assert stored.vehicle_id is None
    assert stored.purchase_price is None
    assert stored.supplier_name is None
    assert stored.version == item["version"]
    assert _events(db_session, item["id"]) == ["inventory.stock_item.added"]
    # Refused before vehicle-mdm is asked for the other car.
    assert _vehicle_mdm_count(db_session) == vehicles_before
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage).where(
        OutboxMessage.event_type == "inventory.pipeline_vehicle.vin_assigned"
    )) == 0


def test_a_configuration_without_a_vin_takes_the_typed_one(client, db_session):
    """A factory order (`build`) has no VIN until the car arrives: the VIN
    typed at purchase booking is the car's, as before KAN-510."""

    token = _token(client)
    item = _pipeline_item(client, token, _configuration(db_session, token, vin=None, mode=ConfigurationMode.BUILD))

    response = _purchase(client, token, item, vin=OTHER_VIN)

    assert response.status_code == 200, response.text
    assert response.json()["lifecycleStatus"] == "in_stock"
    assert response.json()["vin"] == OTHER_VIN


def test_an_item_without_a_configuration_is_booked_as_before(client, db_session):
    """An item the retired hand-typed intake created (no configuration, no
    «Konfiguration zuordnen» yet) has nothing to compare the VIN with."""

    token = _token(client)
    item = create_stock_item(
        db_session, tenant_id=_tenant_of(token), actor_id=None,
        data=StockItemCreate(vehicle_label="VW Golf 8", condition=StockItemCondition.USED),
    )
    assert item.configuration_id is None

    response = _purchase(client, token, {"id": str(item.id), "version": item.version}, vin=OTHER_VIN)

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == OTHER_VIN
