"""KAN-523 — a sold car leaves with its plate: entered at «Rechnung anfordern»
(Anto, 2026-11-01), carried on `sales.contract.invoice_requested`, recorded
on the car's vehicle-mdm record in the delivering Betrieb's group register by
the vehicle consumer, and found by the search box.

A plate current on another car of the group is refused until the user
confirms the move (Anto, 2026-11-01: "Ask before moving, yes"), as Vehicle
360's «Kontrollschild erfassen» does.

Events are delivered the way production delivers them: the real outbox row,
through `consume_once` with the handler `app/worker.py` registers.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.auth import create_access_token
from app.core.config import get_settings
from app.core.consumer import consume_once
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.db import SessionLocal
from app.sales.services.contract import request_invoice
from app.sales.services.invoice_handover import HandoverPlate
from app.vehicle.consumers import handle_invoice_requested_message
from app.vehicle.models.plate import VehiclePlate
from app.vehicle.services.plate import resolve_plate
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.plate_support import seed_plate
from tests.test_sales_manual_configuration_link import (
    _confirmed_manual_contract,
    _dealership,
    _deliver_added,
    _session_factory,
    _stock_buys_it,
    _stock_creates_the_pipeline_item,
)

TODAY = dt.datetime.now(ZoneInfo("Europe/Zurich")).date()
SOLD_VIN = "WVGZZZE2ZNP000004"  # what _stock_buys_it promotes the car with


@pytest.fixture()
def app_sessions_on_the_test_engine(monkeypatch, engine):
    """The plate check runs on a session of its own, from app.db.SessionLocal."""

    monkeypatch.setitem(SessionLocal.kw, "bind", engine)


def _invoiceable_contract(db_session, engine):
    dealership = _dealership(db_session)
    contract = _confirmed_manual_contract(db_session, engine, dealership)
    item = _stock_creates_the_pipeline_item(db_session, contract)
    _deliver_added(db_session, item)
    _stock_buys_it(db_session, item)
    db_session.refresh(contract)
    db_session.refresh(item)
    assert contract.is_invoiceable is True
    return dealership, contract, item


def _invoice_message(db_session, contract) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "sales.contract.invoice_requested", OutboxMessage.aggregate_id == contract.id
        )
    ).one()


def _deliver(db_session, message) -> bool:
    return consume_once(
        db_session, message=message, consumer_name="vehicle.plate_on_invoice_requested",
        handler=handle_invoice_requested_message,
    )


def _other_car(db_session):
    car = create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    db_session.commit()
    return car


def test_the_plate_entered_at_rechnung_anfordern_is_on_the_car_and_found_by_search(db_session, engine):
    dealership, contract, item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    actor = uuid.uuid4()

    request_invoice(
        db_session, contract=contract, actor_id=actor, group_id=group,
        plate=HandoverPlate(plate="zh 123 456", canton="zh"), session_factory=_session_factory(engine),
    )

    message = _invoice_message(db_session, contract)
    payload = message.payload
    assert (payload["plate"], payload["canton"], payload["moveFrom"]) == ("ZH 123456", "ZH", [])
    assert (payload["vehicleId"], payload["vin"], payload["groupId"]) == (str(item.vehicle_id), SOLD_VIN, str(group))
    assert payload["requestedOn"] == TODAY.isoformat()
    assert payload["requestedBy"] == str(actor)
    # Nothing is on the car until vehicle-mdm consumes the event.
    assert resolve_plate(db_session, group_id=group, plate="ZH 123456", canton="ZH") == []

    assert _deliver(db_session, message) is True

    [row] = resolve_plate(db_session, group_id=group, plate="ZH 123456", canton="ZH")
    assert row.vehicle_id == item.vehicle_id
    assert row.valid_from == TODAY  # no delivery date: the day invoicing was requested
    assert row.recording_tenant_id == dealership.id


def test_the_plate_is_valid_from_the_delivery_date(db_session, engine):
    dealership, contract, item = _invoiceable_contract(db_session, engine)
    contract.delivery_date = dt.date(2026, 11, 20)
    db_session.commit()

    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
        plate=HandoverPlate(plate="123456", canton="BE"), session_factory=_session_factory(engine),
    )
    _deliver(db_session, _invoice_message(db_session, contract))

    row = db_session.scalars(select(VehiclePlate).where(VehiclePlate.vehicle_id == item.vehicle_id)).one()
    assert (row.plate, row.valid_from) == ("BE 123456", dt.date(2026, 11, 20))


def test_without_a_plate_nothing_is_recorded(db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)

    request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id)

    message = _invoice_message(db_session, contract)
    assert {k: message.payload[k] for k in ("plate", "canton", "vehicleId", "vin", "moveFrom")} == {
        "plate": None, "canton": None, "vehicleId": None, "vin": None, "moveFrom": [],
    }
    assert _deliver(db_session, message) is True
    assert db_session.scalar(select(func.count()).select_from(VehiclePlate)) == 0


def test_the_request_is_audited_without_the_plate_value(db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    actor = uuid.uuid4()

    request_invoice(
        db_session, contract=contract, actor_id=actor, group_id=dealership.dealer_group_id,
        plate=HandoverPlate(plate="ZH 123456", canton="ZH"), session_factory=_session_factory(engine),
    )

    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == contract.id, AuditEvent.action == "invoice_requested")
    ).one()
    assert audit.actor_id == actor
    assert audit.after == {
        "deliveryDate": None, "requestedOn": TODAY.isoformat(), "withPlate": True, "plateMovedFromOtherVehicle": False,
    }
    assert "123456" not in str(audit.after)


def test_a_plate_on_another_car_of_the_group_is_refused_naming_it_and_nothing_is_published(db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    old_car = _other_car(db_session)
    seed_plate(db_session, vehicle_id=old_car.id, group_id=group, plate="ZH 123456")

    with pytest.raises(ConflictError) as refused:
        request_invoice(
            db_session, contract=contract, actor_id=uuid.uuid4(), group_id=group,
            plate=HandoverPlate(plate="ZH 123456", canton="ZH"), session_factory=_session_factory(engine),
        )
    details = refused.value.details
    assert details["reason"] == "plate_current_on_other_vehicle"
    assert [h["vehicleId"] for h in details["holders"]] == [str(old_car.id)]
    db_session.rollback()
    assert db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type == "sales.contract.invoice_requested")
    ) == 0


def test_the_confirmed_move_ends_the_old_cars_assignment_the_day_before(db_session, engine):
    dealership, contract, item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    old_car = _other_car(db_session)
    old = seed_plate(db_session, vehicle_id=old_car.id, group_id=group, plate="ZH 123456")

    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=group,
        plate=HandoverPlate(plate="ZH 123456", canton="ZH", move_from=[(old.id, old.version)]),
        session_factory=_session_factory(engine),
    )
    message = _invoice_message(db_session, contract)
    assert message.payload["moveFrom"] == [{"plateId": str(old.id), "version": old.version}]
    _deliver(db_session, message)

    db_session.refresh(old)
    assert old.valid_to == TODAY - dt.timedelta(days=1)
    [current] = resolve_plate(db_session, group_id=group, plate="ZH 123456", canton="ZH")
    assert current.vehicle_id == item.vehicle_id


def test_a_stale_answer_is_refused(db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    old_car = _other_car(db_session)
    old = seed_plate(db_session, vehicle_id=old_car.id, group_id=group, plate="ZH 123456")

    with pytest.raises(ConflictError) as refused:
        request_invoice(
            db_session, contract=contract, actor_id=uuid.uuid4(), group_id=group,
            plate=HandoverPlate(plate="ZH 123456", canton="ZH", move_from=[(old.id, old.version + 1)]),
            session_factory=_session_factory(engine),
        )
    assert refused.value.details.get("currentVersion") == old.version


def test_another_groups_plate_is_neither_named_nor_moved(db_session, engine):
    dealership, contract, item = _invoiceable_contract(db_session, engine)
    their_car = _other_car(db_session)
    theirs = seed_plate(db_session, vehicle_id=their_car.id, group_id=uuid.uuid4(), plate="ZH 123456")

    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
        plate=HandoverPlate(plate="ZH 123456", canton="ZH"), session_factory=_session_factory(engine),
    )
    _deliver(db_session, _invoice_message(db_session, contract))

    db_session.refresh(theirs)
    assert theirs.valid_to is None
    [ours] = resolve_plate(db_session, group_id=dealership.dealer_group_id, plate="ZH 123456", canton="ZH")
    assert ours.vehicle_id == item.vehicle_id


def test_a_misspelt_plate_is_refused_before_anything_is_written(db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)

    with pytest.raises(UnprocessableEntityError) as refused:
        request_invoice(
            db_session, contract=contract, actor_id=uuid.uuid4(), group_id=dealership.dealer_group_id,
            plate=HandoverPlate(plate="ZH 0123", canton="ZH"), session_factory=_session_factory(engine),
        )
    assert refused.value.details == {"reason": "plate_invalid"}


def test_a_second_request_with_the_same_plate_records_it_once(db_session, engine):
    dealership, contract, item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    for _ in range(2):
        request_invoice(
            db_session, contract=contract, actor_id=uuid.uuid4(), group_id=group,
            plate=HandoverPlate(plate="ZH 123456", canton="ZH"), session_factory=_session_factory(engine),
        )
    messages = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "sales.contract.invoice_requested")
    ).all()
    assert len(messages) == 2
    for message in messages:
        assert _deliver(db_session, message) is True

    assert db_session.scalar(
        select(func.count()).select_from(VehiclePlate).where(VehiclePlate.vehicle_id == item.vehicle_id)
    ) == 1


def test_a_plate_moved_by_someone_else_meanwhile_is_not_recorded_silently(db_session, engine):
    """The user answered at version 1; before vehicle-mdm consumes the event the
    old car's assignment changed. The consumer refuses (the event is retried,
    then dead-lettered — the alarm reaches a person), never moves a plate on
    an answer the user did not give."""

    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    group = dealership.dealer_group_id
    old_car = _other_car(db_session)
    old = seed_plate(db_session, vehicle_id=old_car.id, group_id=group, plate="ZH 123456")
    request_invoice(
        db_session, contract=contract, actor_id=uuid.uuid4(), group_id=group,
        plate=HandoverPlate(plate="ZH 123456", canton="ZH", move_from=[(old.id, old.version)]),
        session_factory=_session_factory(engine),
    )
    old.version += 1
    db_session.commit()

    with pytest.raises(ConflictError):
        _deliver(db_session, _invoice_message(db_session, contract))
    db_session.rollback()
    db_session.refresh(old)
    assert old.valid_to is None


# --- the HTTP contract the «Rechnung anfordern» dialog uses ------------------------


def _headers(dealership, user=None) -> dict[str, str]:
    token = create_access_token(
        user_id=user or uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _post(client, dealership, contract, body=None, user=None):
    headers = {**_headers(dealership, user), "If-Match": str(contract.version)}
    path = f"/v1/sales/contracts/{contract.id}/request-invoice"
    return client.post(path, json=body, headers=headers) if body is not None else client.post(path, headers=headers)


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_api_the_refusal_names_the_other_car_and_the_answer_moves_it(client, db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    old_car = _other_car(db_session)
    old = seed_plate(db_session, vehicle_id=old_car.id, group_id=dealership.dealer_group_id, plate="ZH 123456")
    user = uuid.uuid4()

    refused = _post(client, dealership, contract, {"plate": "ZH 123456", "canton": "ZH"}, user=user)
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["reason"] == "plate_current_on_other_vehicle"
    [holder] = details["holders"]
    assert (holder["vehicleId"], holder["vin"], holder["version"]) == (str(old_car.id), old_car.vin, old.version)
    # Naming the other car is an audited plate read (ADR-085).
    assert db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.actor_id == user, AuditEvent.entity_id == old_car.id, AuditEvent.reason == "invoice_plate_refused"
        )
    ) == 1

    answered = _post(
        client, dealership, contract,
        {"plate": "ZH 123456", "canton": "ZH", "moveFrom": [{"plateId": holder["plateId"], "version": holder["version"]}]},
        user=user,
    )
    assert answered.status_code == 200, answered.text
    assert _invoice_message(db_session, contract).payload["moveFrom"] == [
        {"plateId": holder["plateId"], "version": holder["version"]}
    ]


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_api_past_the_plate_read_limit_the_refusal_names_no_car(client, db_session, engine, monkeypatch):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)
    old_car = _other_car(db_session)
    seed_plate(db_session, vehicle_id=old_car.id, group_id=dealership.dealer_group_id, plate="ZH 123456")
    monkeypatch.setattr(get_settings(), "plate_read_limit", 0)

    refused = _post(client, dealership, contract, {"plate": "ZH 123456", "canton": "ZH"})
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["reason"] == "plate_current_on_other_vehicle"
    assert details["holdersWithheld"] is True
    assert "holders" not in details


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_api_a_plate_needs_its_canton(client, db_session, engine):
    dealership, contract, _item = _invoiceable_contract(db_session, engine)

    assert _post(client, dealership, contract, {"plate": "123456"}).status_code == 422
    assert _post(client, dealership, contract, {"moveFrom": [{"plateId": str(uuid.uuid4()), "version": 1}]}).status_code == 422


@pytest.mark.usefixtures("app_sessions_on_the_test_engine")
def test_api_a_request_without_a_body_still_hands_over_without_a_plate(client, db_session, engine):
    """The body is optional: the endpoint's earlier contract keeps working."""

    dealership, contract, _item = _invoiceable_contract(db_session, engine)

    response = _post(client, dealership, contract)
    assert response.status_code == 200, response.text
    assert _invoice_message(db_session, contract).payload["plate"] is None
