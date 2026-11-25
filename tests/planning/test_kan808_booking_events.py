"""KAN-808 PR-1c: every booking change raises its outbox event in the
write's transaction, and the four types share one payload shape (ADR-202;
ADR-221 (7))."""

from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.planning import public as planning
from tests import planning_support as p

_KEYS = {
    "bookingId",
    "dealershipId",
    "kind",
    "status",
    "startsAt",
    "endsAt",
    "resources",
    "reference",
    "jobId",
    "vehicleId",
    "note",
    "replan",
    "overruledWarnings",
    "version",
}


def test_the_four_booking_events_share_one_payload_shape(client, db_session):
    shop = p.workshop(client, db_session)
    order, golf, service = p.order("Auftrag 1001"), p.car("ZH 1234 · VW Golf"), p.job("Grosser Service")
    booked = p.work(
        db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.max],
        reference=order, vehicle=golf, for_job=service,
    )
    moved = planning.move(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=booked.booking.id,
        expected_version=1, starts_at=p.at(10), idempotency_key=shop.keys(),
    )
    blocked = p.block(db_session, shop, starts_at=p.at(10), ends_at=p.at(11), resources=[shop.lift_1], note="Defekt")
    planning.release(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=blocked.booking.id,
        expected_version=1, idempotency_key=shop.keys(),
    )

    events = list(
        db_session.scalars(select(OutboxMessage).where(OutboxMessage.producer == "planning").order_by(OutboxMessage.id))
    )
    assert [e.event_type for e in events] == [
        planning.BOOKING_BOOKED_EVENT,
        planning.BOOKING_MOVED_EVENT,
        planning.BOOKING_BOOKED_EVENT,
        planning.BOOKING_MARKED_FOR_REPLANNING_EVENT,
        planning.BOOKING_RELEASED_EVENT,
    ]
    assert all(set(e.payload) == _KEYS for e in events)
    assert all((e.aggregate_type, e.tenant_id) == ("booking", shop.dealership_id) for e in events)

    first = events[0].payload
    assert first == {
        "bookingId": str(booked.booking.id),
        "dealershipId": str(shop.dealership_id),
        "kind": "work",
        "status": "booked",
        "startsAt": "2027-03-01T08:00:00+00:00",
        "endsAt": "2027-03-01T09:00:00+00:00",
        "resources": [
            {"resourceId": str(shop.lift_1), "kind": "workplace"},
            {"resourceId": str(shop.max), "kind": "person"},
        ],
        "reference": {"type": "aftersales.order", "id": str(order.id), "label": "Auftrag 1001"},
        "jobId": str(service.id),
        "vehicleId": str(golf.id),
        "note": None,
        "replan": None,
        "overruledWarnings": [],
        "version": 1,
    }
    marked = events[3].payload
    assert (marked["bookingId"], marked["version"], marked["replan"]["cause"]) == (
        str(moved.booking.id), 3, "blocked",
    )
    assert marked["replan"]["causedByBookingId"] == str(blocked.booking.id)
    block = events[2].payload
    assert (block["kind"], block["note"], block["reference"], block["jobId"]) == ("block", "Defekt", None, None)
    assert (events[4].payload["status"], events[4].payload["version"]) == ("released", 2)
