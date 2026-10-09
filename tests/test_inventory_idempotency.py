"""KAN-266 step 4 (inventory): every inventory POST honours Idempotency-Key.

Per route: the same key twice makes one record (or runs the action once) and
answers the same; the same key with a different request is a 409 for the
key. The mechanism itself is pinned in tests/test_idempotent_route.py.

Reserve and release moved onto IdempotentRoute from a key the reservation
service stored itself (Anto's ruling, 2026-10-08); the service keeps its own
keyed idempotency for Sales and the orphan sweep (tests/test_inventory_reservation.py).
"""

import uuid

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_publishing import StockItemMedia


def _token(tenant_id: uuid.UUID | None = None) -> str:
    tenant_id = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.INVENTORY}),
    )


def _dealership_token(client) -> str:
    """A token whose tenant is a real dealership (the purchase reads it)."""

    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset({AccessRole.PLATFORM_ADMIN})
    )
    response = client.post("/v1/dealerships", json=payload, headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 201, response.text
    return _token(uuid.UUID(response.json()["id"]))


def _headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model).where(*where))


def _assert_key_conflict(response, key: str) -> None:
    """A 409 for the reused key, not for some other conflict."""

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


def _item_body(**overrides) -> dict:
    body = {"vehicleLabel": "Škoda Octavia", "condition": "new"}
    body.update(overrides)
    return body


def _item(client, token: str) -> dict:
    response = client.post("/v1/inventory/stock-items", json=_item_body(), headers=_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- POST /v1/inventory/stock-items ---------------------------------------------


def test_create_stock_item_twice_under_one_key_makes_one_item(client, db_session):
    token = _token()
    label = f"Škoda Octavia {uuid.uuid4().hex[:6]}"

    _twice(client, "/v1/inventory/stock-items", token, _item_body(vehicleLabel=label))

    assert _count(db_session, StockItem, StockItem.vehicle_label == label) == 1


def test_create_stock_item_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _token()
    key, _ = _twice(client, "/v1/inventory/stock-items", token, _item_body())
    other = f"VW Golf {uuid.uuid4().hex[:6]}"

    response = client.post("/v1/inventory/stock-items", json=_item_body(vehicleLabel=other), headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, StockItem, StockItem.vehicle_label == other) == 0


# --- If-Match transitions: condition and purchase --------------------------------


def test_a_retried_condition_change_replays_its_success_instead_of_a_version_conflict(client, db_session):
    token = _token()
    item = _item(client, token)

    _, body = _twice(
        client, f"/v1/inventory/stock-items/{item['id']}/condition", token, {"condition": "demo"}, **{"If-Match": "1"}
    )

    assert body["condition"] == "demo"
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).version == body["version"]


def test_condition_change_with_a_reused_key_and_another_condition_is_a_409(client, db_session):
    token = _token()
    item = _item(client, token)
    path = f"/v1/inventory/stock-items/{item['id']}/condition"
    key, body = _twice(client, path, token, {"condition": "demo"}, **{"If-Match": "1"})

    response = client.post(
        path, json={"condition": "used"}, headers=_headers(token, key, **{"If-Match": str(body["version"])})
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).condition.value == "demo"


_PURCHASE = {
    "supplierName": "Garage Muster AG",
    "supplierIsVatRegistered": True,
    "purchasePrice": "18500.00",
    "purchaseDate": "2026-09-01",
}


def test_a_retried_purchase_replays_its_success_instead_of_a_version_conflict(client, db_session):
    token = _dealership_token(client)
    item = _item(client, token)

    _, body = _twice(client, f"/v1/inventory/stock-items/{item['id']}/purchase", token, _PURCHASE, **{"If-Match": "1"})

    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).version == body["version"]


def test_purchase_with_a_reused_key_and_another_price_is_a_409(client, db_session):
    token = _dealership_token(client)
    item = _item(client, token)
    path = f"/v1/inventory/stock-items/{item['id']}/purchase"
    key, body = _twice(client, path, token, _PURCHASE, **{"If-Match": "1"})

    response = client.post(
        path,
        json={**_PURCHASE, "purchasePrice": "19000.00"},
        headers=_headers(token, key, **{"If-Match": str(body["version"])}),
    )

    _assert_key_conflict(response, key)


# --- POST .../ledger-entries ------------------------------------------------------


def _cost(source_ref: str, amount: str = "350.00") -> dict:
    return {"category": "aufbereitung", "amount": amount, "occurredAt": "2026-09-02T10:00:00Z", "sourceRef": source_ref}


def test_record_cost_twice_under_one_key_makes_one_entry(client, db_session):
    """The ledger is already idempotent by `sourceRef`, so this one holds
    without the key as well; the 409 below is what the key adds."""

    token = _token()
    item = _item(client, token)

    _twice(client, f"/v1/inventory/stock-items/{item['id']}/ledger-entries", token, _cost("inv-4711"))

    assert _count(db_session, StockItemLedger, StockItemLedger.stock_item_id == uuid.UUID(item["id"])) == 1


def test_record_cost_with_a_reused_key_and_another_entry_is_a_409(client, db_session):
    token = _token()
    item = _item(client, token)
    path = f"/v1/inventory/stock-items/{item['id']}/ledger-entries"
    key, _ = _twice(client, path, token, _cost("inv-4711"))

    response = client.post(path, json=_cost("inv-4712", "120.00"), headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, StockItemLedger, StockItemLedger.stock_item_id == uuid.UUID(item["id"])) == 1


# --- publishing: media, reorder, publish, unpublish ---------------------------------


def _media(client, token: str, item_id: str, url: str) -> dict:
    response = client.post(
        f"/v1/inventory/stock-items/{item_id}/media", json={"url": url}, headers=_headers(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_add_media_twice_under_one_key_makes_one_photo(client, db_session):
    token = _token()
    item = _item(client, token)

    _twice(client, f"/v1/inventory/stock-items/{item['id']}/media", token, {"url": "https://cdn.example.ch/1.jpg"})

    assert _count(db_session, StockItemMedia, StockItemMedia.stock_item_id == uuid.UUID(item["id"])) == 1


def test_add_media_with_a_reused_key_and_another_photo_is_a_409(client, db_session):
    token = _token()
    item = _item(client, token)
    path = f"/v1/inventory/stock-items/{item['id']}/media"
    key, _ = _twice(client, path, token, {"url": "https://cdn.example.ch/1.jpg"})

    response = client.post(path, json={"url": "https://cdn.example.ch/2.jpg"}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    assert _count(db_session, StockItemMedia, StockItemMedia.stock_item_id == uuid.UUID(item["id"])) == 1


def test_reorder_media_twice_under_one_key_answers_the_same(client, db_session):
    """Reordering to the order it already has is a no-op, so this one holds
    without the key as well; the 409 below is what the key adds."""

    token = _token()
    item = _item(client, token)
    first = _media(client, token, item["id"], "https://cdn.example.ch/1.jpg")
    second = _media(client, token, item["id"], "https://cdn.example.ch/2.jpg")

    _twice(
        client,
        f"/v1/inventory/stock-items/{item['id']}/media/reorder",
        token,
        {"orderedMediaIds": [second["id"], first["id"]]},
    )


def test_reorder_media_with_a_reused_key_and_another_order_is_a_409(client, db_session):
    token = _token()
    item = _item(client, token)
    first = _media(client, token, item["id"], "https://cdn.example.ch/1.jpg")
    second = _media(client, token, item["id"], "https://cdn.example.ch/2.jpg")
    path = f"/v1/inventory/stock-items/{item['id']}/media/reorder"
    key, _ = _twice(client, path, token, {"orderedMediaIds": [second["id"], first["id"]]})

    response = client.post(path, json={"orderedMediaIds": [first["id"], second["id"]]}, headers=_headers(token, key))

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(StockItemMedia, uuid.UUID(second["id"])).position == 1


def _publishable_item(client, db_session, token: str) -> dict:
    """In stock, with a photo, a price, a colour, a body style and a mileage."""

    response = client.post(
        "/v1/inventory/stock-items",
        json=_item_body(odometerKm=10, effectivePrice="24900.00"),
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    item = response.json()
    _media(client, token, item["id"], "https://cdn.example.ch/1.jpg")
    row = db_session.get(StockItem, uuid.UUID(item["id"]))
    row.lifecycle_status = LifecycleStatus.IN_STOCK
    row.exterior_colour = "Mondsteinsilber"
    row.body_style = "kombi"
    db_session.commit()
    return item


def test_publish_twice_under_one_key_publishes_once(client, db_session):
    token = _token()
    item = _publishable_item(client, db_session, token)

    _, body = _twice(client, f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24/publish", token, None)

    assert body["state"] == "published"


def test_publish_with_a_reused_key_on_another_channel_is_a_409(client, db_session):
    """Publish has no body: what makes it another request is its target."""

    token = _token()
    item = _publishable_item(client, db_session, token)
    key, _ = _twice(client, f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24/publish", token, None)

    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/publishing/carmarket/publish", headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)


def test_unpublish_twice_under_one_key_unpublishes_once(client, db_session):
    token = _token()
    item = _publishable_item(client, db_session, token)
    published = client.post(
        f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24/publish", headers=_headers(token)
    )
    assert published.status_code == 200, published.text

    _, body = _twice(
        client, f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24/unpublish", token, {"confirm": True}
    )

    assert body["state"] == "not_published"


def test_unpublish_with_a_reused_key_and_another_body_is_a_409(client, db_session):
    token = _token()
    item = _publishable_item(client, db_session, token)
    path = f"/v1/inventory/stock-items/{item['id']}/publishing/autoscout24/unpublish"
    key, _ = _twice(client, path, token, {"confirm": True})

    response = client.post(path, json={"confirm": False}, headers=_headers(token, key))

    _assert_key_conflict(response, key)


# --- reserve and release ---------------------------------------------------------------


def _reserve(client, token: str, item_id: str, contract_id: str, key: str | None = None):
    return client.post(
        f"/v1/inventory/stock-items/{item_id}/reservations",
        json={"contractId": contract_id},
        headers=_headers(token, key),
    )


def test_reserve_twice_under_one_key_reserves_once_and_answers_the_same(client, db_session):
    """Run again, the second reserve would find the car reserved and 409."""

    token = _token()
    item = _item(client, token)

    _, body = _twice(
        client, f"/v1/inventory/stock-items/{item['id']}/reservations", token, {"contractId": str(uuid.uuid4())}
    )

    db_session.expire_all()
    row = db_session.get(StockItem, uuid.UUID(item["id"]))
    assert row.reservation_state == ReservationState.RESERVED
    assert str(row.active_reservation_id) == body["reservationId"]


def test_reserve_with_a_reused_key_and_another_contract_is_a_409_for_the_key(client, db_session):
    token = _token()
    item = _item(client, token)
    contract_id = str(uuid.uuid4())
    key = str(uuid.uuid4())
    assert _reserve(client, token, item["id"], contract_id, key).status_code == 201

    response = _reserve(client, token, item["id"], str(uuid.uuid4()), key)

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert str(db_session.get(StockItem, uuid.UUID(item["id"])).reserved_by_contract_id) == contract_id


def test_a_late_retry_of_a_reserve_never_re_reserves_a_released_car(client, db_session):
    """The API convention's promise, kept by the route's record while it
    lives (at least 24 h): a replay returns the first answer and reserves
    nothing, even after the reservation was released."""

    token = _token()
    item = _item(client, token)
    key = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    first = _reserve(client, token, item["id"], contract_id, key)
    assert first.status_code == 201, first.text
    released = client.post(
        f"/v1/inventory/reservations/{first.json()['reservationId']}/release", headers=_headers(token)
    )
    assert released.status_code == 200, released.text

    retry = _reserve(client, token, item["id"], contract_id, key)

    assert retry.status_code == 201
    assert retry.json() == first.json()
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).reservation_state == ReservationState.NONE


def test_release_twice_under_one_key_answers_the_same(client, db_session):
    """Run again, the second release would find no active reservation and 404."""

    token = _token()
    item = _item(client, token)
    reserved = _reserve(client, token, item["id"], str(uuid.uuid4()))
    assert reserved.status_code == 201, reserved.text

    _twice(client, f"/v1/inventory/reservations/{reserved.json()['reservationId']}/release", token, None)

    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).reservation_state == ReservationState.NONE


def test_release_with_a_reused_key_on_another_reservation_is_a_409(client, db_session):
    """Release has no body: what makes it another request is its target."""

    token = _token()
    first_item = _item(client, token)
    other_item = _item(client, token)
    first = _reserve(client, token, first_item["id"], str(uuid.uuid4()))
    other = _reserve(client, token, other_item["id"], str(uuid.uuid4()))
    key, _ = _twice(client, f"/v1/inventory/reservations/{first.json()['reservationId']}/release", token, None)

    response = client.post(
        f"/v1/inventory/reservations/{other.json()['reservationId']}/release", headers=_headers(token, key)
    )

    _assert_key_conflict(response, key)
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(other_item["id"])).reservation_state == ReservationState.RESERVED
