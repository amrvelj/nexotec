"""KAN-810 (rule 10): Aftersales' nightly reconciliation watches every
three-column reference of the workshop order — clean for an order made
through the API, a finding for a reference that names nothing."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from app.aftersales import reconciliation
from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.contact.models.contact import Contact, ContactLifecycleStatus
from app.core.reconciliation import ReferenceCheck, StateCheck, find_orphans, find_rows_in_state
from app.platform.organisation.models.labour_rate import LabourRateCategory
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.aftersales.service_order_support import (
    add_job,
    add_position,
    labour_by_time,
    make_betrieb,
    make_contact,
    make_vehicle,
    new_order,
)


def _findings(db) -> dict[str, list]:
    return {
        check.label: found
        for check in reconciliation.CHECKS
        if isinstance(check, ReferenceCheck) and (found := find_orphans(db, check))
    }


def test_an_order_made_through_the_api_has_no_finding(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    add_position(client, betrieb, order, order["jobs"][0]["id"], **labour_by_time(betrieb, "1"))
    assert _findings(db_session) == {}


def test_a_reference_naming_nothing_is_reported(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    add_position(client, betrieb, order, order["jobs"][0]["id"], **labour_by_time(betrieb, "1"))
    db_session.execute(
        update(ServiceOrder).values(contact_id=uuid.uuid4(), vehicle_id=uuid.uuid4(), location_id=uuid.uuid4())
    )
    db_session.commit()

    labels = set(_findings(db_session))

    assert labels == {
        "service_order.contact_id -> contact.id",
        "service_order.vehicle_id -> vehicle_mdm.id",
        "service_order.location_id -> location.id",
    }
    assert any(
        check.target_model is LabourRateCategory for check in reconciliation.CHECKS if isinstance(check, ReferenceCheck)
    )


# --- a merge, and a contact outside the group (rule 10; slice 1, section 6) ------------------------


_LONG_AGO = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _states(db) -> dict[str, list]:
    return {
        check.label: found
        for check in reconciliation.CHECKS
        if isinstance(check, StateCheck) and (found := find_rows_in_state(db, check))
    }


def _order_with_three_contacts(client, db_session):
    betrieb = make_betrieb(client, db_session)
    recipient = make_contact(db_session, betrieb, name="Flotte AG")
    driver = make_contact(db_session, betrieb, name="Fahrer")
    order = new_order(client, db_session, betrieb, invoiceRecipientContactId=str(recipient.id),
                      driverContactId=str(driver.id))
    return betrieb, order


def _merge_contact(db, contact_id: str, *, at: dt.datetime = _LONG_AGO) -> None:
    db.execute(
        update(Contact).where(Contact.id == uuid.UUID(contact_id))
        .values(lifecycle_status=ContactLifecycleStatus.MERGED, updated_at=at)
    )
    db.commit()


def test_a_clean_order_is_in_no_state_finding(client, db_session):
    _order_with_three_contacts(client, db_session)
    assert _states(db_session) == {}


@pytest.mark.parametrize(
    "column, read",
    [
        ("contact_id", lambda order: order["contact"]["contactId"]),
        ("invoice_recipient_contact_id", lambda order: order["invoiceRecipient"]["contactId"]),
        ("driver_contact_id", lambda order: order["driver"]["contactId"]),
    ],
)
def test_an_order_naming_a_merged_contact_is_reported(client, db_session, column, read):
    _, order = _order_with_three_contacts(client, db_session)
    _merge_contact(db_session, read(order))

    found = _states(db_session)

    assert list(found) == [f"service_order.{column} names a merged contact"]
    assert [row for row, _ in found[f"service_order.{column} names a merged contact"]] == [uuid.UUID(order["id"])]


def test_a_merge_within_its_grace_is_not_yet_a_finding(client, db_session):
    _, order = _order_with_three_contacts(client, db_session)
    _merge_contact(db_session, order["contact"]["contactId"], at=dt.datetime.now(dt.UTC))
    assert _states(db_session) == {}


@pytest.mark.parametrize("column", ["contact_id", "invoice_recipient_contact_id", "driver_contact_id"])
def test_an_order_naming_a_contact_outside_its_group_is_reported(client, db_session, column):
    _, order = _order_with_three_contacts(client, db_session)
    stranger = Contact(group_id=uuid.uuid4(), contact_number=f"K-{uuid.uuid4().hex[:10]}", contact_type="business",
                       company_name="Fremd AG", language="de", lifecycle_status=ContactLifecycleStatus.ACTIVE)
    db_session.add(stranger)
    db_session.commit()
    db_session.execute(
        update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(order["id"])).values({column: stranger.id})
    )
    db_session.commit()

    assert list(_states(db_session)) == [f"service_order.{column} names a contact outside the business's group"]


def test_an_order_naming_a_merged_vehicle_is_reported(client, db_session):
    _, order = _order_with_three_contacts(client, db_session)
    survivor = make_vehicle(db_session)
    db_session.execute(
        update(VehicleMdm).where(VehicleMdm.id == uuid.UUID(order["vehicleId"]))
        .values(merged_into_vehicle_id=survivor, updated_at=_LONG_AGO)
    )
    db_session.commit()

    assert list(_states(db_session)) == ["service_order.vehicle_id names a merged vehicle record"]


def test_a_cancelled_order_is_left_as_it_is(client, db_session):
    _, order = _order_with_three_contacts(client, db_session)
    _merge_contact(db_session, order["contact"]["contactId"])
    db_session.execute(
        update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(order["id"])).values(state=ServiceOrderState.CANCELLED)
    )
    db_session.commit()
    assert _states(db_session) == {}
