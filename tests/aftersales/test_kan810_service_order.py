"""KAN-810 (AS1-07): the workshop order — its number and states (table 3.1),
the people and the car, cancel, the module switch and the capability, the
history. Checks 1, 2, 9, 10 (without the bookings) and 42 (the 404 part) of
Aftersales build slice 1."""

import uuid

import pytest
from sqlalchemy import select, update

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderState
from app.aftersales.services import work_guard
from app.contact.models.contact import ContactLifecycleStatus
from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.outbox_model import OutboxMessage
from app.core.relative_dates import today_in_session_zone
from tests.aftersales.service_order_support import (
    add_job,
    create_order,
    headers,
    make_betrieb,
    make_contact,
    make_vehicle,
    new_order,
)


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


# --- check 1: a number from the range, a draft --------------------------------------------------


def test_check_1_a_new_order_has_its_number_from_the_range_and_is_a_draft(client, db_session, betrieb):
    contact = make_contact(db_session, betrieb, name="Anna Muster AG")
    vehicle_id = make_vehicle(db_session)

    response = create_order(client, betrieb, contact.id, vehicle_id)

    assert response.status_code == 201, response.text
    body = response.json()
    year = today_in_session_zone()[0].year
    assert body["orderNumber"] == f"SA-{year}-00001"  # the shipped seed of `service_order`
    assert body["state"] == "draft"
    assert body["contact"] == {"contactId": str(contact.id), "label": "Anna Muster AG"}
    assert body["invoiceRecipient"]["contactId"] == str(contact.id)  # defaults to the contact
    assert body["vehicleId"] == str(vehicle_id)
    assert body["version"] == 1
    second = create_order(client, betrieb, contact.id, vehicle_id).json()
    assert second["orderNumber"] == f"SA-{year}-00002"

    events = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "aftersales.service_order.created")
    ).all()
    assert {e.payload["orderNumber"] for e in events} == {body["orderNumber"], second["orderNumber"]}
    assert db_session.scalar(
        select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(body["id"]))
    ) == "service_order_created"


# --- check 2: a blocked contact, with the sentence why -----------------------------------------


@pytest.mark.parametrize("role", ["contact", "invoice_recipient"])
def test_check_2_a_blocked_contact_or_invoice_recipient_is_refused_with_the_reason(client, db_session, betrieb, role):
    good = make_contact(db_session, betrieb)
    blocked = make_contact(db_session, betrieb, name="Schuldner GmbH", blocked_reason="Offene Rechnungen seit März")
    vehicle_id = make_vehicle(db_session)

    if role == "contact":
        response = create_order(client, betrieb, blocked.id, vehicle_id)
    else:
        response = create_order(client, betrieb, good.id, vehicle_id, invoiceRecipientContactId=str(blocked.id))

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "service_order_contact_blocked"
    assert details["role"] == role
    assert details["blockedReason"] == "Offene Rechnungen seit März"
    assert "Offene Rechnungen seit März" in response.json()["error"]["message"]
    # Nothing was written, and no number was consumed (ADR-109).
    assert db_session.scalar(select(ServiceOrder.id)) is None
    assert create_order(client, betrieb, good.id, vehicle_id).json()["orderNumber"].endswith("-00001")


def test_a_blocked_driver_is_not_refused(client, db_session, betrieb):
    """Table 3.1 names the contact and the invoice recipient only."""

    contact = make_contact(db_session, betrieb)
    driver = make_contact(db_session, betrieb, name="Fahrer", blocked_reason="Kreditlimite")
    response = create_order(client, betrieb, contact.id, make_vehicle(db_session), driverContactId=str(driver.id))
    assert response.status_code == 201, response.text
    assert response.json()["driver"]["contactId"] == str(driver.id)


def test_do_not_contact_does_not_stop_service(client, db_session, betrieb):
    """Contacts FR-12: do-not-contact never stops service."""

    contact = make_contact(db_session, betrieb, status=ContactLifecycleStatus.DO_NOT_CONTACT)
    assert create_order(client, betrieb, contact.id, make_vehicle(db_session)).status_code == 201


def test_a_merged_contact_is_refused(client, db_session, betrieb):
    contact = make_contact(db_session, betrieb, status=ContactLifecycleStatus.MERGED)
    response = create_order(client, betrieb, contact.id, make_vehicle(db_session))
    assert (response.status_code, _reason(response)) == (409, "service_order_contact_merged")


def test_the_car_must_be_identified(client, db_session, betrieb):
    contact = make_contact(db_session, betrieb)
    response = create_order(client, betrieb, contact.id, None)
    assert (response.status_code, _reason(response)) == (422, "service_order_vehicle_not_identified")
    assert create_order(client, betrieb, contact.id, uuid.uuid4()).status_code == 404


def test_another_groups_contact_is_not_found(client, db_session, betrieb):
    stranger = make_contact(db_session, betrieb, group_id=uuid.uuid4())
    assert create_order(client, betrieb, stranger.id, make_vehicle(db_session)).status_code == 404


def test_another_betriebs_location_is_not_found(client, db_session, betrieb):
    other = make_betrieb(client, db_session)
    contact = make_contact(db_session, betrieb)
    body = {"locationId": str(other.location_id), "contactId": str(contact.id), "vehicleId": str(make_vehicle(db_session))}
    assert client.post("/v1/service-orders", json=body, headers=headers(betrieb)).status_code == 404


def test_another_betriebs_order_is_not_found(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    other = make_betrieb(client, db_session)
    assert client.get(f"/v1/service-orders/{order['id']}", headers=headers(other)).status_code == 404


# --- check 42: the module switch and the capability ---------------------------------------------


def test_check_42_a_betrieb_without_the_module_gets_not_found(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    advisor_sales_only = headers(betrieb, entitlements=frozenset({"sales"}))
    advisor_no_module = headers(betrieb, AccessRole.AFTERSALES, entitlements=frozenset())
    manager_no_module = headers(betrieb, manager=True, entitlements=frozenset({"sales"}))

    for auth in (advisor_sales_only, advisor_no_module, manager_no_module):
        assert client.get("/v1/service-orders", headers=auth).status_code == 404
        assert client.get(f"/v1/service-orders/{order['id']}", headers=auth).status_code == 404
        created = client.post("/v1/service-orders", json={"locationId": str(betrieb.location_id),
                                                           "contactId": order["contact"]["contactId"]}, headers=auth)
        assert created.status_code == 404


def test_check_42_a_role_without_service_orders_cannot_open_the_list(client, db_session, betrieb):
    assert client.get("/v1/service-orders", headers=headers(betrieb, AccessRole.SALES)).status_code == 403
    # The technician has what is on his phone (ruling 180), not the desk's list.
    assert client.get("/v1/service-orders", headers=headers(betrieb, AccessRole.TECHNICIAN)).status_code == 403
    # Parts and finance read; they do not write.
    assert client.get("/v1/service-orders", headers=headers(betrieb, AccessRole.PARTS)).status_code == 200
    contact = make_contact(db_session, betrieb)
    body = {"locationId": str(betrieb.location_id), "contactId": str(contact.id), "vehicleId": str(make_vehicle(db_session))}
    assert client.post("/v1/service-orders", json=body, headers=headers(betrieb, AccessRole.FINANCE)).status_code == 403


# --- checks 9 and 10: cancel ----------------------------------------------------------------------


def test_check_10_a_cancelled_order_shows_who_when_and_why(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)

    response = client.post(
        f"/v1/service-orders/{order['id']}/cancel", json={"reason": "Kunde hat abgesagt"},
        headers=headers(betrieb, if_match=order["version"]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "cancelled"
    assert body["cancellation"]["reason"] == "Kunde hat abgesagt"
    assert body["cancellation"]["cancelledBy"] == str(betrieb.user_id)
    assert body["cancellation"]["cancelledByLabel"] == "Sandra Kundendienst"
    assert body["cancellation"]["cancelledAt"]
    event = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.event_type == "aftersales.service_order.cancelled")
    )
    assert event is not None and event.payload["cancelReason"] == "Kunde hat abgesagt"
    history = client.get(f"/v1/service-orders/{order['id']}/history", headers=headers(betrieb)).json()["items"]
    assert [(e["action"], e["reason"]) for e in history][-1] == ("service_order_cancelled", "Kunde hat abgesagt")


def test_check_9_an_order_with_a_clock_that_has_run_cannot_be_cancelled(client, db_session, betrieb, monkeypatch):
    order = new_order(client, db_session, betrieb)
    asked = []

    def clock_ran(db, *, tenant_id, order_id):
        asked.append(order_id)
        return True

    monkeypatch.setattr(work_guard, "clock_has_run_on_order", clock_ran)
    response = client.post(
        f"/v1/service-orders/{order['id']}/cancel", json={"reason": "Kunde hat abgesagt"},
        headers=headers(betrieb, if_match=order["version"]),
    )

    assert (response.status_code, _reason(response)) == (409, "service_order_clock_has_run")
    assert asked == [uuid.UUID(order["id"])]
    assert client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()["state"] == "draft"


def test_without_a_clock_no_order_counts_as_clocked(db_session):
    """Until KAN-814 builds the clock, no clock has ever run."""

    assert work_guard.clock_has_run_on_order(db_session, tenant_id=uuid.uuid4(), order_id=uuid.uuid4()) is False


def test_a_cancellation_needs_its_reason(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    response = client.post(
        f"/v1/service-orders/{order['id']}/cancel", json={"reason": "   "},
        headers=headers(betrieb, if_match=order["version"]),
    )
    assert (response.status_code, _reason(response)) == (422, "service_order_cancel_reason_required")


# --- table 3.1: frozen orders, If-Match, the people -----------------------------------------------


@pytest.mark.parametrize("state", [ServiceOrderState.CANCELLED, ServiceOrderState.READY_FOR_BILLING])
def test_a_frozen_order_refuses_every_change(client, db_session, betrieb, state):
    order = new_order(client, db_session, betrieb)
    job = add_job(client, betrieb, order)
    db_session.execute(update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(order["id"])).values(state=state))
    db_session.commit()
    auth = headers(betrieb, if_match=job["version"])
    base = f"/v1/service-orders/{order['id']}"
    job_id = job["jobs"][0]["id"]

    for response in (
        client.post(f"{base}/jobs", json={"name": "Neu"}, headers=auth),
        client.patch(f"{base}/jobs/{job_id}", json={"name": "Anders"}, headers=auth),
        client.post(f"{base}/jobs/{job_id}/positions", json={"kind": "text", "text": "Hinweis"}, headers=auth),
        client.patch(base, json={"driverContactId": None}, headers=auth),
        client.post(f"{base}/cancel", json={"reason": "zu spät"}, headers=auth),
    ):
        assert (response.status_code, _reason(response)) == (409, "service_order_frozen"), response.text


def test_a_change_takes_the_orders_version(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    base = f"/v1/service-orders/{order['id']}/jobs"
    assert client.post(base, json={"name": "A"}, headers=headers(betrieb)).status_code == 400
    assert client.post(base, json={"name": "A"}, headers=headers(betrieb, if_match=order["version"])).status_code == 201
    stale = client.post(base, json={"name": "B"}, headers=headers(betrieb, if_match=order["version"]))
    assert stale.status_code == 409


def test_the_people_of_an_open_order_change_and_a_blocked_one_is_refused(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    recipient = make_contact(db_session, betrieb, name="Flotte AG")
    blocked = make_contact(db_session, betrieb, name="Gesperrt AG", blocked_reason="Betreibung")
    base = f"/v1/service-orders/{order['id']}"

    refused = client.patch(base, json={"invoiceRecipientContactId": str(blocked.id)},
                           headers=headers(betrieb, if_match=order["version"]))
    assert (refused.status_code, _reason(refused)) == (409, "service_order_contact_blocked")
    changed = client.patch(base, json={"invoiceRecipientContactId": str(recipient.id)},
                           headers=headers(betrieb, if_match=order["version"]))
    assert changed.status_code == 200, changed.text
    assert changed.json()["invoiceRecipient"] == {"contactId": str(recipient.id), "label": "Flotte AG"}
    assert changed.json()["version"] == order["version"] + 1


# --- the list (D1) and the history ------------------------------------------------------------------


def test_the_list_pages_by_cursor_and_filters_by_chip(client, db_session, betrieb):
    orders = [new_order(client, db_session, betrieb) for _ in range(3)]
    db_session.execute(
        update(ServiceOrder).where(ServiceOrder.id == uuid.UUID(orders[0]["id"]))
        .values(state=ServiceOrderState.READY_FOR_BILLING)
    )
    db_session.commit()

    first = client.get("/v1/service-orders?limit=2", headers=headers(betrieb)).json()
    assert (first["total"], len(first["items"])) == (3, 2)
    assert [i["orderNumber"] for i in first["items"]] == [orders[2]["orderNumber"], orders[1]["orderNumber"]]
    second = client.get(f"/v1/service-orders?limit=2&cursor={first['nextCursor']}", headers=headers(betrieb)).json()
    assert [i["orderNumber"] for i in second["items"]] == [orders[0]["orderNumber"]]

    ready = client.get("/v1/service-orders?chip=ready_for_billing", headers=headers(betrieb)).json()
    assert [i["id"] for i in ready["items"]] == [orders[0]["id"]]
    assert client.get("/v1/service-orders?chip=waiting_for_answer", headers=headers(betrieb)).json()["total"] == 0
    assert client.get("/v1/service-orders?chip=nonsense", headers=headers(betrieb)).status_code == 400
    found = client.get(f"/v1/service-orders?q={orders[1]['orderNumber']}", headers=headers(betrieb)).json()
    assert [i["id"] for i in found["items"]] == [orders[1]["id"]]
    # `%` and `_` are letters in the search, never wildcards.
    for wildcard in ("%25", "_"):
        assert client.get(f"/v1/service-orders?q={wildcard}", headers=headers(betrieb)).json()["total"] == 0


def test_the_history_is_the_audit_trail_of_every_change(client, db_session, betrieb):
    order = new_order(client, db_session, betrieb)
    with_job = add_job(client, betrieb, order)
    client.post(
        f"/v1/service-orders/{order['id']}/jobs/{with_job['jobs'][0]['id']}/positions",
        json={"kind": "text", "text": "Fahrzeug riecht nach Benzin"},
        headers=headers(betrieb, if_match=with_job["version"]),
    )

    history = client.get(f"/v1/service-orders/{order['id']}/history", headers=headers(betrieb)).json()["items"]

    assert [e["action"] for e in history] == [
        "service_order_created", "service_order_job_added", "service_order_position_added",
    ]
    assert {e["actorId"] for e in history} == {str(betrieb.user_id)}


def test_the_manager_reads_and_writes_the_orders(client, db_session, betrieb):
    """Ruling 180: the manager has everything, the module switch still first."""

    order = new_order(client, db_session, betrieb)
    manager = headers(betrieb, manager=True, if_match=order["version"])
    assert client.get("/v1/service-orders", headers=manager).status_code == 200
    assert client.post(f"/v1/service-orders/{order['id']}/jobs", json={"name": "Service"}, headers=manager).status_code == 201
