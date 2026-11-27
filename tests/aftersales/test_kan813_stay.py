"""KAN-813 (AS1-10): the stay of the car and «Fahrzeug ist da» — build slice 1,
sections 3.4 and 3.9; ADR-213; checks 22, 23, 24; the workshop status worked
out, never set (criterion 5)."""

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.aftersales.models.service_order import ServiceOrderState
from app.aftersales.models.service_stay import (
    ArrivalChannel,
    ServiceOrderWorkshopStatus,
    ServiceStay,
    WorkshopStatus,
)
from app.aftersales.services.workshop_status import StatusInputs, work_out
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.vehicle.consumers import handle_stay_arrived_message
from app.vehicle.models.vehicle_history import OdometerSource, VehicleOdometerReading
from app.vehicle.services.vehicle_history import record_odometer_reading
from tests.aftersales.service_order_support import create_order, headers, make_betrieb, make_contact, make_vehicle

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _order(client, db_session, betrieb, vehicle_id=None) -> dict:
    contact = make_contact(db_session, betrieb)
    response = create_order(client, betrieb, contact.id, vehicle_id or make_vehicle(db_session))
    assert response.status_code == 201, response.text
    return response.json()


def _arrive(client, betrieb, order: dict, **body):
    return client.post(f"/v1/service-orders/{order['id']}/arrival", json={"mileage": 42_000, **body},
                       headers=headers(betrieb, if_match=order["version"]))


def _get(client, betrieb, order: dict) -> dict:
    return client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()


# --- check 22: «Fahrzeug ist da» needs a mileage; a lower one is taken and flagged ---------------------


def test_check_22_the_arrival_records_who_when_how_and_what_was_taken_in(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    response = _arrive(client, betrieb, order, keyTag="K-17", parkingPlace="P 4", remark="Rundgang entfällt")
    assert response.status_code == 201, response.text
    stay = response.json()["stay"]
    assert (stay["arrivalMileage"], stay["keyTag"], stay["parkingPlace"], stay["remark"]) == (
        42_000, "K-17", "P 4", "Rundgang entfällt",
    )
    # Criterion 6: who, when, how.
    assert (stay["arrivedBy"], stay["arrivedByLabel"], stay["arrivalChannel"]) == (
        str(betrieb.user_id), "Sandra Kundendienst", "desk",
    )
    assert stay["arrivedAt"]
    assert stay["departedAt"] is None
    assert stay["orders"] == [{"id": order["id"], "orderNumber": order["orderNumber"]}]


def test_check_22_a_mileage_is_required_and_a_whole_number_from_0(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    missing = client.post(f"/v1/service-orders/{order['id']}/arrival", json={},
                          headers=headers(betrieb, if_match=order["version"]))
    assert missing.status_code == 422
    negative = _arrive(client, betrieb, order, mileage=-1)
    assert (negative.status_code, _reason(negative)) == (422, "service_stay_mileage_invalid")
    fraction = _arrive(client, betrieb, order, mileage=12.5)
    assert fraction.status_code == 422
    assert db_session.scalar(select(ServiceStay.id)) is None


def test_check_22_a_lower_mileage_than_the_last_is_taken_and_flagged(client, db_session, betrieb):
    car = make_vehicle(db_session)
    record_odometer_reading(db_session, vehicle_id=car, value=50_000, reading_date=utcnow().date(),
                            source=OdometerSource.MANUAL, recording_tenant_id=betrieb.id)
    order = _order(client, db_session, betrieb, car)
    assert order["vehicleMileage"] == 50_000  # «Letzter Stand» beside D14

    response = _arrive(client, betrieb, order, mileage=40_000)
    assert response.status_code == 201, response.text
    stay = response.json()["stay"]
    assert (stay["arrivalMileage"], stay["previousMileage"], stay["mileageBelowPrevious"]) == (40_000, 50_000, True)

    _deliver_arrival(db_session)
    reading = db_session.scalar(
        select(VehicleOdometerReading).where(VehicleOdometerReading.source == OdometerSource.SERVICE_VISIT)
    )
    assert reading is not None and (reading.value, reading.implausible) == (40_000, True)


# --- check 23: a car in the house is not received twice; a second order joins the stay -------------------


def test_check_23_a_car_that_is_here_cannot_be_received_a_second_time(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    order = _arrive(client, betrieb, order).json()
    again = _arrive(client, betrieb, order)
    assert (again.status_code, _reason(again)) == (409, "service_stay_car_already_here")


def test_check_23_a_second_order_for_a_car_that_is_here_joins_the_stay_and_shows_it(client, db_session, betrieb):
    car = make_vehicle(db_session)
    first = _arrive(client, betrieb, _order(client, db_session, betrieb, car)).json()

    second = _order(client, db_session, betrieb, car)

    assert second["stay"]["id"] == first["stay"]["id"]
    assert second["workshopStatus"] == "car_here"
    assert {o["orderNumber"] for o in second["stay"]["orders"]} == {first["orderNumber"], second["orderNumber"]}
    refused = _arrive(client, betrieb, second)
    assert (refused.status_code, _reason(refused)) == (409, "service_stay_car_already_here")
    history = client.get(f"/v1/service-orders/{second['id']}/history", headers=headers(betrieb)).json()["items"]
    assert "service_order_joined_stay" in [item["action"] for item in history]


def test_every_open_order_of_the_car_joins_the_stay_at_the_arrival(client, db_session, betrieb):
    car = make_vehicle(db_session)
    first = _order(client, db_session, betrieb, car)
    other = _order(client, db_session, betrieb, car)
    cancelled = _order(client, db_session, betrieb, car)
    client.post(f"/v1/service-orders/{cancelled['id']}/cancel", json={"reason": "doppelt"},
                headers=headers(betrieb, if_match=cancelled["version"]))

    stay_id = _arrive(client, betrieb, first).json()["stay"]["id"]

    assert _get(client, betrieb, other)["stay"]["id"] == stay_id
    assert _get(client, betrieb, other)["workshopStatus"] == "car_here"
    assert _get(client, betrieb, cancelled)["stay"] is None


def test_the_same_car_at_another_betrieb_is_its_own_stay(client, db_session, betrieb):
    car = make_vehicle(db_session)
    _arrive(client, betrieb, _order(client, db_session, betrieb, car))
    other = make_betrieb(client, db_session)
    elsewhere = _arrive(client, other, _order(client, db_session, other, car))
    assert elsewhere.status_code == 201, elsewhere.text


def test_one_running_stay_per_car_and_betrieb_is_held_by_the_database(db_session):
    tenant_id, vehicle_id, now = uuid.uuid4(), uuid.uuid4(), utcnow()

    def stay(departed=None) -> ServiceStay:
        return ServiceStay(
            tenant_id=tenant_id, vehicle_id=vehicle_id, vehicle_label="VW", vehicle_label_refreshed_at=now,
            location_id=uuid.uuid4(), location_label="Aarau", location_label_refreshed_at=now, arrived_at=now,
            arrival_channel=ArrivalChannel.DESK, arrival_mileage=1, departed_at=departed,
        )

    db_session.add_all([stay(departed=now), stay()])  # a stay that ended and a running one: fine
    db_session.commit()
    db_session.add(stay())
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_frozen_order_receives_no_car(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    order = client.post(f"/v1/service-orders/{order['id']}/cancel", json={"reason": "x"},
                        headers=headers(betrieb, if_match=order["version"])).json()
    refused = _arrive(client, betrieb, order)
    assert (refused.status_code, _reason(refused)) == (409, "service_order_frozen")


def test_the_arrival_takes_the_orders_version_and_is_not_found_elsewhere(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    stale = client.post(f"/v1/service-orders/{order['id']}/arrival", json={"mileage": 1},
                        headers=headers(betrieb, if_match=order["version"] + 5))
    assert stale.status_code == 409
    other = make_betrieb(client, db_session)
    assert client.post(f"/v1/service-orders/{order['id']}/arrival", json={"mileage": 1},
                       headers=headers(other, if_match=order["version"])).status_code == 404


# --- check 24: the mileage reaches the car's record through an event -------------------------------------


def _deliver_arrival(db_session) -> OutboxMessage:
    message = db_session.scalar(select(OutboxMessage).where(OutboxMessage.event_type == "aftersales.stay.arrived"))
    assert message is not None
    handle_stay_arrived_message(db_session, message)
    db_session.commit()
    return message


def test_check_24_the_mileage_of_the_reception_is_on_the_cars_record(client, db_session, betrieb):
    car = make_vehicle(db_session)
    order = _order(client, db_session, betrieb, car)
    stay = _arrive(client, betrieb, order, mileage=61_234).json()["stay"]

    message = _deliver_arrival(db_session)

    assert message.tenant_id == betrieb.id
    assert message.aggregate_id == uuid.UUID(stay["id"])
    payload = message.payload
    assert (payload["vehicleId"], payload["arrivalMileage"], payload["stayId"]) == (str(car), 61_234, stay["id"])
    reading = db_session.scalar(select(VehicleOdometerReading).where(VehicleOdometerReading.vehicle_id == car))
    assert reading is not None
    assert (reading.value, reading.source, reading.recording_tenant_id, reading.implausible) == (
        61_234, OdometerSource.SERVICE_VISIT, betrieb.id, False,
    )
    assert reading.reading_date.isoformat() == payload["arrivedOn"]


# --- criterion 5: the workshop status, worked out, read by the list from its projection ----------------


def test_the_list_reads_the_workshop_status_from_its_projection(client, db_session, betrieb):
    order = _order(client, db_session, betrieb)
    assert order["workshopStatus"] == "planned"
    _arrive(client, betrieb, order)

    items = client.get("/v1/service-orders", headers=headers(betrieb)).json()["items"]
    assert [(item["id"], item["workshopStatus"]) for item in items] == [(order["id"], "car_here")]
    row = db_session.scalar(select(ServiceOrderWorkshopStatus))
    assert row is not None and row.status is WorkshopStatus.CAR_HERE


def _inputs(**overrides) -> StatusInputs:
    base = {"order_state": ServiceOrderState.ACCEPTED, "car_here": True, "car_left": False, "clock_running": False,
            "labour_count": 2, "labour_done": 0, "check_needed": False, "check_passed": False}
    return StatusInputs(**{**base, **overrides})


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"car_here": False}, WorkshopStatus.PLANNED),
        ({"car_here": False, "car_left": True}, WorkshopStatus.HANDED_OVER),
        ({}, WorkshopStatus.CAR_HERE),
        ({"labour_count": 0}, WorkshopStatus.CAR_HERE),
        ({"clock_running": True}, WorkshopStatus.IN_WORK),
        ({"labour_done": 1}, WorkshopStatus.IN_WORK),
        ({"labour_done": 2, "check_needed": True}, WorkshopStatus.WORK_DONE),
        ({"labour_done": 2}, WorkshopStatus.CHECKED),
        ({"labour_done": 2, "check_needed": True, "check_passed": True}, WorkshopStatus.CHECKED),
        ({"labour_done": 2, "order_state": ServiceOrderState.READY_FOR_BILLING}, WorkshopStatus.READY_FOR_PICKUP),
        ({"labour_done": 2, "clock_running": True}, WorkshopStatus.IN_WORK),
    ],
)
def test_the_workshop_status_is_worked_out_from_its_inputs(overrides, status):
    assert work_out(_inputs(**overrides)) is status


def test_no_write_path_sets_the_workshop_status():
    """Criterion 5: no request body names a workshop status, no route writes it, and the
    projection has one writer, `workshop_status.refresh`."""

    from app.aftersales.schemas import service_order as order_schemas
    from app.aftersales.schemas import service_package as package_schemas
    from app.main import app

    for module in (order_schemas, package_schemas):
        for name, model in vars(module).items():
            fields = getattr(model, "model_fields", None)
            if isinstance(fields, dict) and (name.endswith(("Create", "Update", "Cancel", "Copy")) or "Entry" in name):
                assert "workshop_status" not in fields, name
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        assert not ("workshop" in path and methods & {"POST", "PUT", "PATCH", "DELETE"}), path

    writers = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ServiceOrderWorkshopStatus":
                writers.add(path.relative_to(ROOT).as_posix())
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    elements = target.elts if isinstance(target, ast.Tuple) else [target]
                    for element in elements:
                        if isinstance(element, ast.Attribute) and element.attr == "status" and "workshop" in path.name:
                            writers.add(path.relative_to(ROOT).as_posix())
    assert writers == {"app/aftersales/services/workshop_status.py"}
