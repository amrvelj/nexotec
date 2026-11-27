"""KAN-838: a position PATCH whose only field is `timeUnit` answered 200 and
changed nothing — `_refuse_change_shape` did not track it. A time unit is
checked against the time it came with (ADR-226), never relabelled, so sent
alone it is refused 422 `service_order_position_incomplete`, like every other
field a change cannot apply."""

import pytest

from tests.aftersales.service_order_support import (
    add_job,
    add_position,
    headers,
    labour_by_time,
    make_betrieb,
    new_order,
)


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _position(client, db_session, betrieb, **position):
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    order = add_position(client, betrieb, order, order["jobs"][0]["id"], **position).json()
    return order, order["jobs"][0]["positions"][-1]


def _patch(client, betrieb, order, position, body):
    return client.patch(
        f"/v1/service-orders/{order['id']}/positions/{position['id']}",
        json=body,
        headers=headers(betrieb, if_match=order["version"]),
    )


def _refusal(response) -> tuple[int, str, str]:
    details = response.json()["error"]["details"]
    return response.status_code, details["reason"], details["field"]


def test_a_time_unit_alone_on_a_text_position_is_refused(client, db_session, betrieb):
    order, position = _position(client, db_session, betrieb, kind="text", text="Hinweis")

    response = _patch(client, betrieb, order, position, {"timeUnit": "hours"})

    assert _refusal(response) == (422, "service_order_position_incomplete", "kind")


def test_a_time_unit_alone_on_a_labour_position_is_refused(client, db_session, betrieb):
    order, position = _position(client, db_session, betrieb, **labour_by_time(betrieb, "1.50"))

    response = _patch(client, betrieb, order, position, {"timeUnit": "hours"})

    assert _refusal(response) == (422, "service_order_position_incomplete", "time")
    unchanged = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    assert unchanged["version"] == order["version"]


def test_a_time_unit_sent_with_a_time_is_still_taken(client, db_session, betrieb):
    order, position = _position(client, db_session, betrieb, **labour_by_time(betrieb, "1.50"))

    response = _patch(client, betrieb, order, position, {"time": "2", "timeUnit": "hours"})

    assert response.status_code == 200, response.text
    assert response.json()["jobs"][0]["positions"][-1]["time"]["quantity"] in ("2", "2.00")
