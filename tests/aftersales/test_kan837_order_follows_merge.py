"""KAN-837 (follow-up of KAN-810; slice section 6): a workshop order follows
a contact or vehicle merge and refreshes its labels.

- `aftersales.contact_merged` moves every order naming the duplicate as
  customer, invoice recipient or driver to the end of the survivor's chain.
- `aftersales.vehicle_merged` moves every order naming the merged record.
- `aftersales.order_labels` refreshes the five labels nightly, on every order.
- After the consumers ran, PR-1's StateChecks find nothing.
"""

import datetime as dt
import uuid

from sqlalchemy import select, update

from app.aftersales import reconciliation
from app.aftersales.consumers import (
    CONTACT_MERGED_CONSUMER,
    VEHICLE_MERGED_CONSUMER,
    handle_contact_merged_message,
    handle_vehicle_merged_message,
)
from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.aftersales.services.order_references import oldest_order_label_age_seconds, refresh_order_labels
from app.contact.models.contact import Contact
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from app.core.audit_model import AuditEvent
from app.core.consumer import consume_once
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import StateCheck, find_rows_in_state
from app.platform.organisation.models.dealership import Location
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.merge import MERGED_EVENT, merge_vehicles
from app.worker import register_daily_jobs, register_handlers
from tests.aftersales.service_order_support import make_betrieb, make_contact, make_vehicle, new_order
from tests.cross_context.test_consumer_redelivery_idempotency import _RecordingTransport

_LONG_AGO = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _merge_contact(db, *, duplicate_id, survivor_id, group_id) -> OutboxMessage:
    db.expire_all()  # make_contact's rows hold plain strings until they are read back
    loaded = contact_common_service.get_contact_or_404(db, group_id, duplicate_id)
    contact_merge_service.merge_contact(
        db, contact=loaded, duplicate_of_contact_id=survivor_id, actor_id=uuid.uuid4(), acting_dealership_id=None
    )
    return db.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "contact.merged", OutboxMessage.aggregate_id == duplicate_id)
    ).one()


def _merge_vehicle(db, *, duplicate_id, survivor_id) -> OutboxMessage:
    merge_vehicles(db, survivor_id=survivor_id, duplicate_id=duplicate_id, actor_id=uuid.uuid4())
    db.commit()
    return db.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == MERGED_EVENT, OutboxMessage.aggregate_id == duplicate_id)
    ).one()


def _deliver_contact(db, message) -> bool:
    return consume_once(db, message=message, consumer_name=CONTACT_MERGED_CONSUMER, handler=handle_contact_merged_message)


def _deliver_vehicle(db, message) -> bool:
    return consume_once(db, message=message, consumer_name=VEHICLE_MERGED_CONSUMER, handler=handle_vehicle_merged_message)


def _order(db, order_id) -> ServiceOrder:
    db.expire_all()
    return db.get(ServiceOrder, uuid.UUID(str(order_id)))


def _states(db) -> dict[str, list]:
    return {
        check.label: found
        for check in reconciliation.CHECKS
        if isinstance(check, StateCheck) and (found := find_rows_in_state(db, check))
    }


def _age(db, model, row_id) -> None:
    """Make a merge older than the StateChecks' day of grace."""

    db.execute(update(model).where(model.id == row_id).values(updated_at=_LONG_AGO))
    db.commit()


def test_the_consumers_and_the_label_job_are_registered(monkeypatch):
    transport = _RecordingTransport()
    register_handlers(transport)
    assert transport.registrations[CONTACT_MERGED_CONSUMER][0] == "contact.merged"
    assert transport.registrations[VEHICLE_MERGED_CONSUMER][0] == "vehicle.vehicle_mdm.merged"
    names: list[str] = []
    monkeypatch.setattr("app.worker.register_daily_job", lambda name, job: names.append(name))
    register_daily_jobs()
    assert names.index("aftersales.order_labels") < names.index("reconciliation.run_all")


def test_a_contact_merge_moves_every_role_of_an_order_to_the_survivor(client, db_session):
    betrieb = make_betrieb(client, db_session)
    duplicate = make_contact(db_session, betrieb, name="Flotte Dublette AG")
    survivor = make_contact(db_session, betrieb, name="Flotte AG")
    order = new_order(
        client, db_session, betrieb, invoiceRecipientContactId=str(duplicate.id), driverContactId=str(duplicate.id)
    )
    customer_only = new_order(client, db_session, betrieb)
    message = _merge_contact(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=betrieb.group_id)

    assert _deliver_contact(db_session, message)
    db_session.commit()

    moved = _order(db_session, order["id"])
    assert moved.invoice_recipient_contact_id == survivor.id and moved.invoice_recipient_label == "Flotte AG"
    assert moved.driver_contact_id == survivor.id and moved.driver_label == "Flotte AG"
    assert moved.contact_id == uuid.UUID(order["contact"]["contactId"])  # the customer was not merged
    assert moved.version == order["version"] + 1
    assert _order(db_session, customer_only["id"]).version == customer_only["version"]
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == moved.id, AuditEvent.action == "service_order_contact_merged")
    ).one()
    assert audit.before["invoiceRecipientContactId"] == str(duplicate.id)
    assert audit.after["invoiceRecipientContactId"] == str(survivor.id)
    # PR-1's StateChecks: nothing left to report once the merge is old.
    _age(db_session, Contact, duplicate.id)
    assert _states(db_session) == {}


def test_a_redelivery_moves_nothing_twice(client, db_session):
    betrieb = make_betrieb(client, db_session)
    duplicate = make_contact(db_session, betrieb, name="Dublette")
    survivor = make_contact(db_session, betrieb, name="Echt")
    order = new_order(client, db_session, betrieb, invoiceRecipientContactId=str(duplicate.id))
    message = _merge_contact(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=betrieb.group_id)

    assert _deliver_contact(db_session, message)
    db_session.commit()
    version = _order(db_session, order["id"]).version
    assert not _deliver_contact(db_session, message)
    handle_contact_merged_message(db_session, message)  # the handler again by hand: nothing left to move
    db_session.commit()
    assert _order(db_session, order["id"]).version == version


def test_a_chain_merge_delivered_out_of_order_lands_on_the_end_of_the_chain(client, db_session):
    betrieb = make_betrieb(client, db_session)
    first = make_contact(db_session, betrieb, name="Erste")
    middle = make_contact(db_session, betrieb, name="Mitte")
    end = make_contact(db_session, betrieb, name="Ende AG")
    order = new_order(client, db_session, betrieb, invoiceRecipientContactId=str(first.id))
    first_into_middle = _merge_contact(db_session, duplicate_id=first.id, survivor_id=middle.id, group_id=betrieb.group_id)
    middle_into_end = _merge_contact(db_session, duplicate_id=middle.id, survivor_id=end.id, group_id=betrieb.group_id)

    assert _deliver_contact(db_session, middle_into_end)
    assert _deliver_contact(db_session, first_into_middle)
    db_session.commit()

    moved = _order(db_session, order["id"])
    assert moved.invoice_recipient_contact_id == end.id and moved.invoice_recipient_label == "Ende AG"


def test_a_frozen_order_moves_too_and_takes_the_survivors_name(client, db_session):
    """The criterion: every order naming the duplicate moves and takes the
    survivor's label — a ready-for-billing order still names its invoice recipient."""

    betrieb = make_betrieb(client, db_session)
    duplicate = make_contact(db_session, betrieb, name="Dublette")
    survivor = make_contact(db_session, betrieb, name="Echt AG")
    order = new_order(client, db_session, betrieb, invoiceRecipientContactId=str(duplicate.id))
    db_session.execute(
        update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(order["id"])).values(state=ServiceOrderState.READY_FOR_BILLING)
    )
    db_session.commit()
    message = _merge_contact(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=betrieb.group_id)

    assert _deliver_contact(db_session, message)
    db_session.commit()

    moved = _order(db_session, order["id"])
    assert moved.invoice_recipient_contact_id == survivor.id
    assert moved.invoice_recipient_label == "Echt AG"
    assert moved.state is ServiceOrderState.READY_FOR_BILLING


def test_a_vehicle_merge_moves_the_order_to_the_record_the_car_lives_on(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = new_order(client, db_session, betrieb)
    survivor = make_vehicle(db_session)
    duplicate_id = uuid.UUID(order["vehicleId"])
    message = _merge_vehicle(db_session, duplicate_id=duplicate_id, survivor_id=survivor)

    assert _deliver_vehicle(db_session, message)
    db_session.commit()
    assert not _deliver_vehicle(db_session, message)

    moved = _order(db_session, order["id"])
    survivor_vin = db_session.get(VehicleMdm, survivor).vin
    assert moved.vehicle_id == survivor and moved.vehicle_label == survivor_vin
    assert moved.version == order["version"] + 1
    assert db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_id == moved.id, AuditEvent.action == "service_order_vehicle_merged")
    ).one()
    _age(db_session, VehicleMdm, duplicate_id)
    assert _states(db_session) == {}


def test_the_nightly_job_relabels_every_order(client, db_session):
    betrieb = make_betrieb(client, db_session)
    open_order = new_order(client, db_session, betrieb)
    frozen = new_order(client, db_session, betrieb)
    db_session.execute(
        update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(frozen["id"])).values(state=ServiceOrderState.CANCELLED)
    )
    for order in (open_order, frozen):
        db_session.get(Contact, uuid.UUID(order["contact"]["contactId"])).company_name = "Neu AG"
    db_session.get(Location, betrieb.location_id).name = "Filiale Nord"
    db_session.execute(update(ServiceOrder).values(contact_label_refreshed_at=_LONG_AGO))
    db_session.commit()
    assert oldest_order_label_age_seconds(db_session) > 300 * 86400

    report = refresh_order_labels(db_session)

    relabelled = _order(db_session, open_order["id"])
    assert relabelled.contact_label == "Neu AG" and relabelled.invoice_recipient_label == "Neu AG"
    assert relabelled.location_label == "Filiale Nord"
    assert relabelled.version == open_order["version"]  # a copy refreshed on purpose: no version bump
    cancelled = _order(db_session, frozen["id"])
    assert cancelled.contact_label == "Neu AG" and cancelled.contact_label_refreshed_at > _LONG_AGO
    assert {(row, column) for row, column, _, _ in report.changed} >= {
        (relabelled.id, "contact_label"), (relabelled.id, "location_label")
    }
    assert oldest_order_label_age_seconds(db_session) < 60


def test_the_nightly_job_leaves_a_merged_contact_alone(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = new_order(client, db_session, betrieb)
    contact_id = uuid.UUID(order["contact"]["contactId"])
    survivor = make_contact(db_session, betrieb, name="Echt AG")
    _merge_contact(db_session, duplicate_id=contact_id, survivor_id=survivor.id, group_id=betrieb.group_id)
    db_session.commit()

    report = refresh_order_labels(db_session)

    assert (uuid.UUID(order["id"]), "contact_label") in report.unresolved
    assert _order(db_session, order["id"]).contact_id == contact_id  # the consumer moves it, not the job


def test_a_vehicle_chain_merge_delivered_out_of_order_lands_on_the_live_record(client, db_session):
    """A→B is delivered after B→C: the order lands on C, never on the merged B."""

    betrieb = make_betrieb(client, db_session)
    order = new_order(client, db_session, betrieb)
    first = uuid.UUID(order["vehicleId"])
    middle, end = make_vehicle(db_session), make_vehicle(db_session)
    first_into_middle = _merge_vehicle(db_session, duplicate_id=first, survivor_id=middle)
    middle_into_end = _merge_vehicle(db_session, duplicate_id=middle, survivor_id=end)

    assert _deliver_vehicle(db_session, middle_into_end)
    assert _deliver_vehicle(db_session, first_into_middle)
    db_session.commit()

    assert _order(db_session, order["id"]).vehicle_id == end


def test_the_nightly_job_relabels_a_car_whose_record_changed(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = new_order(client, db_session, betrieb)
    record = db_session.get(VehicleMdm, uuid.UUID(order["vehicleId"]))
    record.vin = "WVWZZZ1KZAW837099"
    db_session.commit()

    refresh_order_labels(db_session)

    assert _order(db_session, order["id"]).vehicle_label == "WVWZZZ1KZAW837099"
