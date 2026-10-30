"""KAN-446 (ADR-045, FR-V-04; Anto, 2026-10-25): purchase booking is what
puts a car on stock. A pipeline item cannot be bought without its VIN; with
one, `POST /v1/inventory/stock-items/{id}/purchase` promotes it into stock
(the vehicle-mdm record found or created) in the same request, and the item
is invoiceable at once. Over HTTP, on Postgres.
"""

import uuid

from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem
from tests.configuration_test_support import stock_configuration_id
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

VIN = "WVWZZZ1KZAW004461"

_PURCHASE = {
    "supplierName": "Hans Muster",
    "supplierIsVatRegistered": False,
    "purchasePrice": "8000.00",
    "purchaseDate": "2026-10-30",
}


def _token(client) -> str:
    """An inventory token whose tenant is a real dealership (the purchase reads its VAT rate)."""

    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset({AccessRole.PLATFORM_ADMIN})
    )
    response = client.post("/v1/dealerships", json=payload, headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 201, response.text
    tenant_id = response.json()["id"]
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), group_id=dealer_group_of(tenant_id),
        roles=frozenset({AccessRole.INVENTORY}),
    )


def _auth(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _create(client, db_session, token: str, **body: str) -> dict:
    body = {"vehicleLabel": "VW Golf (Eintausch)", "condition": "used", "configurationId": stock_configuration_id(db_session, token), **body}
    response = client.post("/v1/inventory/stock-items", json=body, headers=_auth(token))
    assert response.status_code == 201, response.text
    return response.json()


def _purchase(client, token: str, item: dict, **extra: str):
    return client.post(
        f"/v1/inventory/stock-items/{item['id']}/purchase",
        json={**_PURCHASE, **extra},
        headers=_auth(token, **{"If-Match": str(item["version"])}),
    )


def _events(db_session, item_id: str) -> list[str]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OutboxMessage.event_type)
            .where(OutboxMessage.aggregate_id == uuid.UUID(item_id))
            .order_by(OutboxMessage.created_at)
        )
    )


def test_a_pipeline_item_without_a_vin_cannot_be_bought(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)

    response = _purchase(client, token, item)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "purchase_requires_vin", "stockItemId": item["id"]}
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert stored.lifecycle_status.value == "pipeline"
    assert stored.purchase_price is None
    assert stored.version == item["version"]


def test_buying_a_pipeline_item_with_its_vin_puts_it_on_stock_and_makes_it_invoiceable(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)

    # Spelt as a person types it: normalised before anything is read or written.
    response = _purchase(client, token, item, vin=" wvwzzz1kzaw 004461 ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lifecycleStatus"] == "in_stock"
    assert body["vin"] == VIN
    assert body["vehicleId"] is not None
    assert body["purchasePrice"] == "8000.00"
    assert body["isInvoiceable"] is True
    assert _events(db_session, item["id"]).count("inventory.pipeline_vehicle.vin_assigned") == 1
    assert _events(db_session, item["id"]).count("inventory.stock_item.purchased") == 1

    # The car is now this Betrieb's vehicle-mdm record, found by its VIN.
    found = client.get(f"/v1/vehicle-mdm/{body['vehicleId']}", headers=_auth(token))
    assert found.status_code == 200, found.text
    assert found.json()["vin"] == VIN


def test_a_vin_another_item_holds_in_stock_is_refused_and_nothing_is_booked(client, db_session):
    token = _token(client)
    holder = _create(client, db_session, token, vin=VIN)
    item = _create(client, db_session, token)

    response = _purchase(client, token, item, vin=VIN)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vin_already_in_stock"
    assert details["stockItemId"] == holder["id"]
    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert stored.lifecycle_status.value == "pipeline"
    assert stored.vin is None
    assert stored.purchase_price is None
    assert _events(db_session, item["id"]).count("inventory.stock_item.purchased") == 0


def test_a_malformed_vin_is_refused(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token)

    response = _purchase(client, token, item, vin="WVWZZZ1KZAW00446O")  # an O is never in a VIN

    assert response.status_code == 422, response.text


def test_an_item_in_stock_is_bought_without_a_vin_or_with_its_own_but_not_with_another(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token, vin=VIN)

    other = _purchase(client, token, item, vin="WVWZZZ1KZAW004462")
    assert other.status_code == 422, other.text
    assert other.json()["error"]["details"] == {"reason": "purchase_vin_mismatch", "stockItemId": item["id"]}

    own = _purchase(client, token, item, vin=VIN.lower())
    assert own.status_code == 200, own.text
    assert own.json()["isInvoiceable"] is True

    second = _create(client, db_session, token, vin="WVWZZZ1KZAW004463")
    without = _purchase(client, token, second)
    assert without.status_code == 200, without.text
    assert without.json()["isInvoiceable"] is True
