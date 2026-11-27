"""KAN-848: the workshop order list on the list-query layer (ADR-179) —
every column the list shows sorts server-side, words under the collation
(ADR-190), and `filter` works over the same registry."""

import uuid

import pytest
from sqlalchemy import update

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.aftersales.services.order_grid import SERVICE_ORDER_COLUMNS
from tests.aftersales.service_order_support import create_order, headers, make_betrieb, make_contact, make_vehicle
from tests.text_sort_support import text_sort_order

# Ä beside A, case ignored: a byte-order sort would put «Zahnd» before «ägerter».
NAMES = ["Zahnd AG", "ägerter GmbH", "Bucher SA", "aebi & Co"]


@pytest.fixture()
def orders(client, db_session):
    betrieb = make_betrieb(client, db_session)
    made = []
    for name in NAMES:
        response = create_order(client, betrieb, make_contact(db_session, betrieb, name=name).id, make_vehicle(db_session))
        assert response.status_code == 201, response.text
        made.append(response.json())
    return betrieb, made


def _list(client, betrieb, **params):
    response = client.get("/v1/service-orders", params=params, headers=headers(betrieb))
    return response


def test_the_grid_schema_serves_every_column_and_each_sorts(client, db_session, orders):
    betrieb, _ = orders
    response = client.get("/v1/service-orders/grid-schema", headers=headers(betrieb))

    assert response.status_code == 200, response.text
    fields = response.json()["fields"]
    assert [f["field"] for f in fields] == [spec.api_name for spec in SERVICE_ORDER_COLUMNS]
    assert [f["field"] for f in fields] == [
        "orderNumber", "vehicleLabel", "contactLabel", "state", "workshopStatus", "orderDate", "locationLabel",
        "createdAt",
    ]
    assert all(f["sortable"] for f in fields)
    state = next(f for f in fields if f["field"] == "state")
    assert [o["value"] for o in state["options"]] == [s.value for s in ServiceOrderState]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_the_ordering_party_sorts_under_the_collation(client, db_session, orders, direction):
    betrieb, _ = orders
    response = _list(client, betrieb, sort=f"contactLabel:{direction}")

    assert response.status_code == 200, response.text
    shown = [item["contact"]["label"] for item in response.json()["items"]]
    assert shown == text_sort_order(db_session, NAMES, descending=direction == "desc")


@pytest.mark.parametrize("field", [spec.api_name for spec in SERVICE_ORDER_COLUMNS])
def test_every_field_sorts_and_pages_by_cursor(client, db_session, orders, field):
    betrieb, made = orders
    first = _list(client, betrieb, sort=f"{field}:asc", limit=2)
    assert first.status_code == 200, first.text
    second = _list(client, betrieb, sort=f"{field}:asc", limit=2, cursor=first.json()["nextCursor"])
    assert second.status_code == 200, second.text

    ids = [item["id"] for page in (first, second) for item in page.json()["items"]]
    assert sorted(ids) == sorted(order["id"] for order in made)


def test_the_vehicle_sorts_under_the_collation(client, db_session, orders):
    betrieb, made = orders
    response = _list(client, betrieb, sort="vehicleLabel:desc")
    shown = [item["vehicleLabel"] for item in response.json()["items"]]
    assert shown == text_sort_order(db_session, [order["vehicleLabel"] for order in made], descending=True)


def test_filter_over_the_registry_and_the_state_parameter_agree(client, db_session, orders):
    betrieb, made = orders
    cancelled = uuid.UUID(made[1]["id"])
    db_session.execute(update(ServiceOrder).where(ServiceOrder.id == cancelled).values(state=ServiceOrderState.CANCELLED))
    db_session.commit()

    by_filter = _list(client, betrieb, filter="state:is:cancelled")
    by_state = _list(client, betrieb, state="cancelled")
    excluded = _list(client, betrieb, filter="state:isNot:cancelled")
    by_name = _list(client, betrieb, filter="contactLabel:contains:BUCHER")

    assert [i["id"] for i in by_filter.json()["items"]] == [str(cancelled)]
    assert [i["id"] for i in by_state.json()["items"]] == [str(cancelled)]
    assert str(cancelled) not in [i["id"] for i in excluded.json()["items"]] and excluded.json()["total"] == 3
    assert [i["contact"]["label"] for i in by_name.json()["items"]] == ["Bucher SA"]


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"sort": "invoiceRecipientLabel:asc"}, "unknown_field"),
        ({"filter": "nope:is:x"}, "unknown_field"),
        ({"filter": "state:contains:x"}, "operator_not_allowed"),
        ({"filter": "state:is:lost"}, "invalid_filter_value"),
    ],
)
def test_anything_outside_the_registry_is_refused(client, db_session, orders, params, reason):
    betrieb, _ = orders
    response = _list(client, betrieb, **params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason


def test_another_betriebs_orders_are_not_listed(client, db_session, orders):
    _, _ = orders
    other = make_betrieb(client, db_session)
    response = _list(client, other, sort="contactLabel:asc")
    assert response.status_code == 200 and response.json()["items"] == []
