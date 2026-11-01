"""KAN-467 — recording a Kontrollschild: the API (record, confirm the move,
Wechselschild, end), the group's register seen through the Plates tab and
the search box, and the delivery consumer. The end-to-end test the ticket
names: record ZH 123456 on a car, search «ZH 123456», get that car.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from app.core.outbox import OutboxEvent, publish
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.vehicle.consumers import handle_sales_contract_delivered_message
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

OUR_BETRIEB = uuid.uuid4()
OUR_GROUP = uuid.uuid4()
THEIR_BETRIEB = uuid.uuid4()
THEIR_GROUP = uuid.uuid4()
# The service's own "today" (utcnow) — what the search resolves against.
TODAY = dt.datetime.now(dt.UTC).date()


def _headers(*, tenant=OUR_BETRIEB, group=OUR_GROUP, user=None, **extra) -> dict[str, str]:
    token = create_access_token(
        user_id=user or uuid.uuid4(), tenant_id=tenant, group_id=group, roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _cars(db_session, count=2):
    vins = ["ZAR94000007123456", "WVWZZZ1JZXW000001", "1HGCM82633A004352"][:count]
    cars = [create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None) for vin in vins]
    db_session.commit()
    return cars


def _record(client, car, body=None, **header_kwargs):
    payload = {"plate": "ZH 123456", "canton": "ZH", "validFrom": TODAY.isoformat(), **(body or {})}
    return client.post(f"/v1/vehicle-mdm/{car.id}/plates", json=payload, headers=_headers(**header_kwargs))


def test_a_recorded_plate_is_found_by_the_search_box(client, db_session):
    """KAN-467 criterion 6 — the end-to-end test that would have caught it."""

    [car, _other] = _cars(db_session)

    recorded = _record(client, car, {"plate": "zh 123 456", "canton": "zh"})
    assert recorded.status_code == 201, recorded.text
    assert (recorded.json()["plate"], recorded.json()["canton"], recorded.json()["version"]) == ("ZH 123456", "ZH", 1)

    body = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=_headers()).json()
    assert body["resolved"]["id"] == str(car.id)
    assert body["resolved"]["currentPlate"] == "ZH 123456"
    tab = client.get(f"/v1/vehicle-mdm/{car.id}/plates", headers=_headers())
    assert [p["plate"] for p in tab.json()] == ["ZH 123456"]


def test_another_group_neither_sees_nor_moves_our_plate(client, db_session):
    ours, theirs = _cars(db_session)
    assert _record(client, ours).status_code == 201

    # Their group records the same plate on another car: no refusal, no move.
    assert _record(client, theirs, tenant=THEIR_BETRIEB, group=THEIR_GROUP).status_code == 201

    ours_search = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=_headers()).json()
    assert ours_search["resolved"]["id"] == str(ours.id)
    their_headers = _headers(tenant=THEIR_BETRIEB, group=THEIR_GROUP)
    their_search = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=their_headers).json()
    assert their_search["resolved"]["id"] == str(theirs.id)
    # Our car's Plates tab, read from their group, shows nothing of ours.
    assert client.get(f"/v1/vehicle-mdm/{ours.id}/plates", headers=their_headers).json() == []
    lookup = client.get("/v1/vehicle-mdm/lookup", params={"vin": ours.vin}, headers=their_headers).json()
    assert lookup["currentPlate"] is None


def test_a_plate_on_another_car_of_the_group_moves_once_the_user_confirms(client, db_session):
    old_car, new_car = _cars(db_session)
    held = _record(client, old_car, {"validFrom": "2024-03-01"}).json()

    refused = _record(client, new_car)
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["reason"] == "plate_current_on_other_vehicle"
    [holder] = details["holders"]
    assert (holder["plateId"], holder["vehicleNumber"], holder["vin"]) == (
        held["id"], old_car.vehicle_number, old_car.vin,
    )

    moved = _record(client, new_car, {"moveFromPlateIds": [held["id"]]})
    assert moved.status_code == 201, moved.text
    [old_row] = client.get(f"/v1/vehicle-mdm/{old_car.id}/plates", headers=_headers()).json()
    assert old_row["validTo"] == (TODAY - dt.timedelta(days=1)).isoformat()
    search = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=_headers()).json()
    assert search["resolved"]["id"] == str(new_car.id)


def test_a_wechselschild_is_formed_on_request(client, db_session):
    car_a, car_b = _cars(db_session)
    first = _record(client, car_a, {"plate": "TG 41277", "canton": "TG"}).json()

    second = _record(client, car_b, {"plate": "TG 41277", "canton": "TG", "wechselschildWithPlateId": first["id"]})
    assert second.status_code == 201, second.text
    assert second.json()["isInterchangeable"] is True

    search = client.get("/v1/vehicle-mdm/search", params={"q": "TG 41277"}, headers=_headers()).json()
    assert search["resolved"] is None
    assert {c["id"] for c in search["pickerCandidates"]} == {str(car_a.id), str(car_b.id)}
    assert all(not c["isConflict"] for c in search["pickerCandidates"])


@pytest.mark.parametrize(
    ("body", "reason"),
    [({"canton": "XX"}, "plate_canton_invalid"), ({"plate": "ZH 0815"}, "plate_invalid"),
     ({"validTo": "2020-01-01"}, "plate_validity_invalid")],
)
def test_what_is_no_kontrollschild_is_refused_in_the_users_terms(client, db_session, body, reason):
    [car] = _cars(db_session, 1)
    response = _record(client, car, body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason


def test_ending_an_assignment_takes_if_match_and_another_groups_row_is_a_404(client, db_session):
    [car] = _cars(db_session, 1)
    row = _record(client, car, {"validFrom": "2024-03-01"}).json()
    url = f"/v1/vehicle-mdm/{car.id}/plates/{row['id']}/end"
    body = {"validTo": TODAY.isoformat()}

    assert client.post(url, json=body, headers=_headers()).status_code == 400
    assert client.post(url, json=body, headers=_headers(**{"If-Match": "9"})).status_code == 409
    their = _headers(tenant=THEIR_BETRIEB, group=THEIR_GROUP, **{"If-Match": "1"})
    assert client.post(url, json=body, headers=their).status_code == 404

    ended = client.post(url, json=body, headers=_headers(**{"If-Match": "1"}))
    assert ended.status_code == 200, ended.text
    assert (ended.json()["validTo"], ended.json()["version"]) == (TODAY.isoformat(), 2)
    assert db_session.get(VehiclePlate, uuid.UUID(row["id"])) is not None


def test_a_retried_recording_under_one_idempotency_key_records_once(client, db_session):
    [car] = _cars(db_session, 1)
    key = {"Idempotency-Key": str(uuid.uuid4())}
    user = uuid.uuid4()

    first = _record(client, car, user=user, **key)
    second = _record(client, car, user=user, **key)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert db_session.scalar(select(func.count()).select_from(VehiclePlate)) == 1


def test_a_recording_is_audited_with_its_author_and_without_the_plate_value(client, db_session):
    [car] = _cars(db_session, 1)
    user = uuid.uuid4()
    row = _record(client, car, user=user).json()

    [event] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(row["id"]), AuditEvent.action == "plate_recorded")
    ).all()
    assert (event.actor_id, event.tenant_id) == (user, OUR_BETRIEB)
    assert "123456" not in repr((event.before, event.after, event.reason))


# --- the delivery consumer (criterion 3: the vehicle-mdm side and the event contract) ---------


def _delivered(db_session, car, **payload):
    message = publish(
        db_session,
        OutboxEvent(
            event_type="sales.contract.delivered",
            tenant_id=OUR_BETRIEB,
            producer="sales",
            aggregate_type="sales_contract",
            aggregate_id=uuid.uuid4(),
            payload={
                "contractNumber": "V-2026-0001",
                "vehicleId": str(car.id),
                "groupId": str(OUR_GROUP),
                "deliveredOn": TODAY.isoformat(),
                "deliveredBy": None,
                "plate": "ZH 123456",
                "canton": "ZH",
                "moveFromPlateIds": [],
                **payload,
            },
        ),
    )
    db_session.commit()
    return message


def _deliver(engine, message):
    transport = InProcessTransport(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    transport.register(
        "sales.contract.delivered", consumer_name="vehicle.plate_on_contract_delivered",
        handler=handle_sales_contract_delivered_message,
    )
    transport.deliver(message)


def test_a_car_delivered_with_its_plate_is_found_by_that_plate(client, db_session, engine):
    [car] = _cars(db_session, 1)
    _deliver(engine, _delivered(db_session, car))

    search = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=_headers()).json()
    assert search["resolved"]["id"] == str(car.id)


def test_a_delivery_without_a_plate_or_with_one_already_recorded_records_nothing(client, db_session, engine):
    [car] = _cars(db_session, 1)
    _deliver(engine, _delivered(db_session, car, plate=None, canton=None))
    assert db_session.scalar(select(func.count()).select_from(VehiclePlate)) == 0

    assert _record(client, car).status_code == 201
    _deliver(engine, _delivered(db_session, car))
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(VehiclePlate)) == 1


def test_a_delivery_never_moves_a_plate_the_user_did_not_confirm(client, db_session, engine):
    old_car, new_car = _cars(db_session)
    assert _record(client, old_car, {"validFrom": "2024-03-01"}).status_code == 201

    with pytest.raises(DeliveryError):
        _deliver(engine, _delivered(db_session, new_car))
    db_session.expire_all()
    [old_row] = db_session.scalars(select(VehiclePlate)).all()
    assert old_row.valid_to is None


def test_a_delivery_moves_the_plate_the_user_confirmed(client, db_session, engine):
    old_car, new_car = _cars(db_session)
    held = _record(client, old_car, {"validFrom": "2024-03-01"}).json()

    _deliver(engine, _delivered(db_session, new_car, moveFromPlateIds=[held["id"]]))

    search = client.get("/v1/vehicle-mdm/search", params={"q": "ZH 123456"}, headers=_headers()).json()
    assert search["resolved"]["id"] == str(new_car.id)
