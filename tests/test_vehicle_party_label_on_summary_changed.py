"""KAN-186 — a change to a vehicle's label reaches the customer's party rows
through an event, not the next night (CLAUDE.md rules 2, 4 and 6).

What is pinned here:
- vehicle-mdm publishes `vehicle.vehicle_mdm.summary_changed` in the same
  transaction as a write that changes a field `VehicleSummary` carries (a
  VIN correction; a first registration in another year), with the whole
  summary and the names of the fields that changed;
- a write that changes no summary field (a Stammnummer, a status, a first
  registration in the same year) publishes nothing, and a write that is
  rolled back publishes nothing;
- the customer consumer relabels every party row that names the vehicle —
  every dealer group's, the vehicle being a global fact (ADR-022) — moves
  `vehicle_label_refreshed_at`, and a redelivered event changes nothing;
- the label-age gauge still reads the stalest stamp (exit criterion 3).
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.processed_event_model import ProcessedEvent
from app.customer.consumers import handle_vehicle_summary_changed_message
from app.customer.models.vehicle_party import VehicleParty
from app.customer.services.customer import oldest_vehicle_party_label_age_seconds
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.vehicle_mdm import VehicleMdmUpdate
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service
from app.vehicle.services.vehicle_mdm import SUMMARY_CHANGED_EVENT
from tests.test_customer_vehicle import _bearer, _create_customer, _create_dealer, _create_vehicle, _random_vin, _token
from tests.test_vehicle_party_vehicle_label import _link, _list, _match, _party, _variant

_CONSUMER = "customer.vehicle_party_labels_on_summary_changed"


def _patch(client, dealer_id: str, vehicle: dict, body: dict):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    return client.patch(
        f"/v1/vehicle-mdm/{vehicle['id']}", json=body,
        headers={**_bearer(token), "If-Match": str(vehicle["version"])},
    )


def _events(db, vehicle_id) -> list[OutboxMessage]:
    db.expire_all()
    return list(
        db.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == SUMMARY_CHANGED_EVENT, OutboxMessage.aggregate_id == uuid.UUID(str(vehicle_id)))
            .order_by(OutboxMessage.created_at)
        ).all()
    )


def _transport(engine) -> InProcessTransport:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(SUMMARY_CHANGED_EVENT, consumer_name=_CONSUMER, handler=handle_vehicle_summary_changed_message)
    return transport


def _setup(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id, firstRegistrationDate="2020-05-01")
    party = _link(client, dealer_id, customer["id"], vehicle["id"])
    return dealer_id, customer, vehicle, party


# --- the producer ----------------------------------------------------------------------


def test_a_vin_correction_publishes_the_summary_in_its_own_transaction(client, db_session):
    dealer_id, _customer, vehicle, _party_body = _setup(client)
    corrected = _random_vin()

    response = _patch(client, dealer_id, vehicle, {"vin": corrected})

    assert response.status_code == 200, response.text
    [event] = _events(db_session, vehicle["id"])
    assert event.producer == "vehicle"
    assert event.aggregate_type == "vehicle_mdm"
    assert event.tenant_id is None  # vehicle-mdm is global (ADR-022): no tenant owns the fact
    assert event.payload == {
        "vehicleId": vehicle["id"],
        "version": response.json()["version"],
        "changedFields": ["vin"],
        "summary": {
            "vin": corrected, "vehicleNumber": vehicle["vehicleNumber"],
            "make": None, "model": None, "modelYear": 2020, "trim": None,
        },
    }


def test_a_first_registration_in_another_year_publishes_the_model_year(client, db_session):
    dealer_id, _customer, vehicle, _party_body = _setup(client)
    _match(db_session, vehicle["id"], _variant(db_session))

    response = _patch(client, dealer_id, vehicle, {"firstRegistrationDate": "2022-03-01"})

    assert response.status_code == 200, response.text
    [event] = _events(db_session, vehicle["id"])
    assert event.payload["changedFields"] == ["modelYear"]
    assert event.payload["summary"] == {
        "vin": vehicle["vin"], "vehicleNumber": vehicle["vehicleNumber"],
        "make": "Volkswagen", "model": "Golf", "modelYear": 2022, "trim": "2.0 TSI GTI",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"stammnummer": "123456789"},
        {"vehicleStatus": "exported"},
        {"firstRegistrationDate": "2020-11-30"},  # the same year: the summary's modelYear is unchanged
        {"vin": "__same__"},  # the stored VIN again
    ],
    ids=["stammnummer", "status", "same-year", "same-vin"],
)
def test_a_write_that_changes_no_summary_field_publishes_nothing(client, db_session, body):
    dealer_id, _customer, vehicle, _party_body = _setup(client)
    if body.get("vin") == "__same__":
        body = {"vin": vehicle["vin"]}

    response = _patch(client, dealer_id, vehicle, body)

    assert response.status_code == 200, response.text
    assert response.json()["version"] == vehicle["version"] + 1  # the write itself happened
    assert _events(db_session, vehicle["id"]) == []


def test_a_refused_vin_correction_publishes_nothing(client, db_session):
    """The duplicate-VIN path rolls the write back; its event goes with it."""

    dealer_id, _customer, vehicle, _party_body = _setup(client)
    other = _create_vehicle(client, dealer_id)

    response = _patch(client, dealer_id, vehicle, {"vin": other["vin"]})

    assert response.status_code == 409, response.text
    assert _events(db_session, vehicle["id"]) == []


def test_the_event_and_the_write_commit_together(client, db_session, engine, monkeypatch):
    """One transaction: when writing the outbox row fails, the VIN
    correction is not committed either — never a dual write."""

    _dealer_id, _customer, vehicle, _party_body = _setup(client)

    def failing_publish(db, event):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(vehicle_mdm_service, "publish", failing_publish)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as db:
        row = db.get(VehicleMdm, uuid.UUID(vehicle["id"]))
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            vehicle_mdm_service.update_vehicle_mdm(db, vehicle=row, data=VehicleMdmUpdate(vin=_random_vin()), actor_id=None)
        db.rollback()

    db_session.expire_all()
    stored = db_session.get(VehicleMdm, uuid.UUID(vehicle["id"]))
    assert (stored.vin, stored.version) == (vehicle["vin"], vehicle["version"])
    assert _events(db_session, vehicle["id"]) == []


# --- the consumer ----------------------------------------------------------------------


def test_the_consumer_relabels_the_party_row_and_moves_its_stamp(client, db_session, engine):
    dealer_id, customer, vehicle, party_body = _setup(client)
    before = _party(db_session, party_body["id"])
    stamped_at, updated_at = before.vehicle_label_refreshed_at, before.updated_at
    corrected = _random_vin()
    patched = _patch(client, dealer_id, vehicle, {"vin": corrected}).json()
    patched = _patch(client, dealer_id, patched, {"firstRegistrationDate": "2023-02-01"}).json()

    assert _party(db_session, party_body["id"]).vehicle_vin == vehicle["vin"]  # not yet: the event is pending

    for event in _events(db_session, vehicle["id"]):
        _transport(engine).deliver(event)

    after = _party(db_session, party_body["id"])
    assert (after.vehicle_vin, after.vehicle_number, after.vehicle_model_year) == (
        corrected, vehicle["vehicleNumber"], 2023,
    )
    assert after.vehicle_label_refreshed_at > stamped_at
    assert after.updated_at > updated_at  # what the Vehicles tab returns changed
    listed = _list(client, dealer_id, customer["id"])[0]["vehicle"]
    assert (listed["vin"], listed["modelYear"]) == (corrected, 2023)


def test_the_consumer_relabels_every_groups_row_for_the_vehicle(client, db_session, engine):
    """vehicle-mdm is global and each dealer group keeps its own holders
    (KAN-99): every group's row names the same car, so each takes its label."""

    dealer_id, _customer, vehicle, own_party = _setup(client)
    other_dealer = _create_dealer(client)
    other_customer = _create_customer(client, other_dealer)
    other_party = _link(client, other_dealer, other_customer["id"], vehicle["id"])
    unrelated = _create_vehicle(client, dealer_id)
    unrelated_party = _link(client, dealer_id, _customer["id"], unrelated["id"], role="keeper")
    unrelated_stamp = _party(db_session, unrelated_party["id"]).vehicle_label_refreshed_at
    corrected = _random_vin()
    _patch(client, dealer_id, vehicle, {"vin": corrected})

    [event] = _events(db_session, vehicle["id"])
    _transport(engine).deliver(event)

    assert _party(db_session, own_party["id"]).vehicle_vin == corrected
    assert _party(db_session, other_party["id"]).vehicle_vin == corrected
    assert _party(db_session, unrelated_party["id"]).vehicle_label_refreshed_at == unrelated_stamp


def test_the_consumer_writes_the_vehicles_current_label_not_an_older_events(client, db_session, engine):
    """At-least-once and unordered: an older event delivered after a newer
    one must not put its stale values back. The consumer reads the label
    from vehicle.public, so either order ends on the current VIN."""

    dealer_id, _customer, vehicle, party_body = _setup(client)
    first, second = _random_vin(), _random_vin()
    patched = _patch(client, dealer_id, vehicle, {"vin": first}).json()
    _patch(client, dealer_id, patched, {"vin": second})
    older, newer = _events(db_session, vehicle["id"])
    assert older.payload["summary"]["vin"] == first

    _transport(engine).deliver(newer)
    _transport(engine).deliver(older)

    assert _party(db_session, party_body["id"]).vehicle_vin == second


def test_a_redelivered_event_is_a_no_op(client, db_session, engine):
    dealer_id, _customer, vehicle, party_body = _setup(client)
    _patch(client, dealer_id, vehicle, {"vin": _random_vin()})
    [event] = _events(db_session, vehicle["id"])
    transport = _transport(engine)

    transport.deliver(event)
    first = _party(db_session, party_body["id"])
    first_stamp, first_updated = first.vehicle_label_refreshed_at, first.updated_at
    transport.deliver(event)

    again = _party(db_session, party_body["id"])
    assert (again.vehicle_label_refreshed_at, again.updated_at) == (first_stamp, first_updated)
    assert db_session.scalar(
        select(func.count()).select_from(ProcessedEvent).where(
            ProcessedEvent.event_id == event.id, ProcessedEvent.consumer_name == _CONSUMER
        )
    ) == 1


def test_an_unchanged_row_is_restamped_without_moving_updated_at(client, db_session, engine):
    """A row already carrying the new label (a link made after the change)
    is only checked: nothing a reader sees changed — the nightly job's rule."""

    dealer_id, customer, vehicle, party_body = _setup(client)
    corrected = _random_vin()
    _patch(client, dealer_id, vehicle, {"vin": corrected})
    late = _link(client, dealer_id, customer["id"], vehicle["id"], role="driver")
    late_before = _party(db_session, late["id"])
    late_stamp, late_updated = late_before.vehicle_label_refreshed_at, late_before.updated_at
    assert late_before.vehicle_vin == corrected

    [event] = _events(db_session, vehicle["id"])
    _transport(engine).deliver(event)

    late_after = _party(db_session, late["id"])
    assert late_after.vehicle_label_refreshed_at > late_stamp
    assert late_after.updated_at == late_updated
    assert _party(db_session, party_body["id"]).vehicle_vin == corrected


def test_an_event_for_a_vehicle_no_party_names_changes_nothing(client, db_session, engine):
    dealer_id = _create_dealer(client)
    vehicle = _create_vehicle(client, dealer_id)
    _patch(client, dealer_id, vehicle, {"vin": _random_vin()})
    [event] = _events(db_session, vehicle["id"])

    _transport(engine).deliver(event)

    assert db_session.scalar(select(func.count()).select_from(VehicleParty)) == 0
    assert db_session.scalar(
        select(func.count()).select_from(ProcessedEvent).where(ProcessedEvent.event_id == event.id)
    ) == 1


# --- exit criterion 3: the label-age alarm ---------------------------------------------


def test_the_label_age_gauge_reads_the_stalest_stamp_after_the_consumer(client, db_session, engine):
    """`dms.label.age_seconds{label="vehicle_party.vehicle"}` still measures
    the oldest stamp: the consumer restamps only the vehicle it names, so a
    row it did not touch keeps the gauge's age."""

    dealer_id, customer, vehicle, party_body = _setup(client)
    other = _create_vehicle(client, dealer_id)
    stale = _link(client, dealer_id, customer["id"], other["id"], role="keeper")
    db_session.execute(
        VehicleParty.__table__.update()
        .where(VehicleParty.id == uuid.UUID(stale["id"]))
        .values(vehicle_label_refreshed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=3))
    )
    db_session.commit()
    _patch(client, dealer_id, vehicle, {"vin": _random_vin()})
    [event] = _events(db_session, vehicle["id"])

    _transport(engine).deliver(event)

    age = oldest_vehicle_party_label_age_seconds(db_session)
    assert age is not None and age >= dt.timedelta(days=3).total_seconds() - 60
    assert _party(db_session, party_body["id"]).vehicle_label_refreshed_at > dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)


def test_the_worker_registers_the_consumer():
    from app.worker import register_handlers

    transport = InProcessTransport(session_factory=None)  # type: ignore[arg-type]
    register_handlers(transport)
    assert _CONSUMER in transport.registered_consumer_names()
