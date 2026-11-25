"""KAN-808 PR-1c: the planner's rules through `planning.public` (ADR-221 (2);
slice 1, section 3.8; Anto, 2026-11-25).

Refused: one car at two workplaces, another car on a taken workplace (check
16), one person twice on one job (check 17). Warned and bookable: outside the
person's working hours (check 18), before the drop-off, after the pick-up, a
blocked resource — refused until confirmed, then kept on the booking with the
name and the time, and in the audit log. A block is always possible and marks
the bookings it hits; a moved slot marks the work it newly leaves outside it;
a deactivated resource marks its bookings to come."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox_model import OutboxMessage
from app.planning import public as planning
from app.planning.models.booking import Booking, OverruledWarning
from app.planning.models.resource import WorkingHours
from app.planning.services import resources as resource_service
from app.planning.services.booking_rules import outside_working_hours
from app.platform.organisation.models.dealership import Location
from tests import planning_support as p
from tests.identity_support import create_dealership

W = planning.WarningCode


@pytest.fixture()
def shop(client, db_session) -> p.Workshop:
    return p.workshop(client, db_session)


def _refused(call, error=ConflictError) -> dict:
    with pytest.raises(error) as raised:
        call()
    return raised.value.details or {}


def _events(db, event_type: str | None = None) -> list[OutboxMessage]:
    stmt = select(OutboxMessage).where(OutboxMessage.producer == "planning")
    if event_type is not None:
        stmt = stmt.where(OutboxMessage.event_type == event_type)
    return list(db.scalars(stmt.order_by(OutboxMessage.id)))


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- check 16 -------------------------------------------------------------------------------


def test_one_car_at_two_workplaces_at_once_is_refused(db_session, shop):
    golf = p.car()
    first = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], vehicle=golf)
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(9, 30), ends_at=p.at(10, 30), resources=[shop.lift_2], vehicle=golf)
    )
    assert details == {"reason": "planning_car_at_another_workplace", "bookingIds": [str(first.booking.id)]}
    assert _count(db_session, Booking) == 1


def test_another_car_on_a_taken_workplace_is_refused(db_session, shop):
    first = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    details = _refused(lambda: p.work(db_session, shop, starts_at=p.at(9, 45), ends_at=p.at(11), resources=[shop.lift_1]))
    assert details == {
        "reason": "planning_workplace_taken",
        "bookingIds": [str(first.booking.id)],
        "resourceId": str(shop.lift_1),
    }


def test_two_jobs_on_one_car_at_one_workplace_and_touching_bookings_are_booked(db_session, shop):
    golf = p.car()
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], vehicle=golf)
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], vehicle=golf)
    # [10:00, 11:00) only touches [09:00, 10:00): another car may follow on the lift.
    p.work(db_session, shop, starts_at=p.at(10), ends_at=p.at(11), resources=[shop.lift_1])
    assert _count(db_session, Booking) == 3


def test_a_released_booking_frees_the_workplace(db_session, shop):
    first = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    released = planning.release(
        db_session,
        dealership_id=shop.dealership_id,
        actor_id=shop.actor_id,
        booking_id=first.booking.id,
        expected_version=first.booking.version,
        idempotency_key=shop.keys(),
    )
    assert (released.status, released.version, released.resources[0].resource_id) == (
        planning.BookingStatus.RELEASED, 2, shop.lift_1,
    )
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    details = _refused(
        lambda: planning.release(
            db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=first.booking.id,
            expected_version=2, idempotency_key=shop.keys(),
        )
    )
    assert details == {"reason": "planning_booking_released"}
    assert [e.payload["status"] for e in _events(db_session, "planning.booking.released")] == ["released"]


def test_the_same_car_is_checked_within_the_betrieb_only(client, db_session, shop):
    other = p.workshop(client, db_session)
    golf = p.car()
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], vehicle=golf)
    p.work(db_session, other, starts_at=p.at(9), ends_at=p.at(10), resources=[other.lift_2], vehicle=golf)
    assert _count(db_session, Booking) == 2


# --- check 17 -------------------------------------------------------------------------------


def test_two_people_on_one_job_are_booked_and_one_person_twice_on_it_is_refused(db_session, shop):
    service = p.job()
    golf = p.car()
    first = p.work(
        db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.lea, shop.max],
        vehicle=golf, for_job=service,
    )
    assert [r.kind for r in first.booking.resources] == [
        planning.ResourceKind.WORKPLACE, planning.ResourceKind.PERSON, planning.ResourceKind.PERSON,
    ]
    details = _refused(
        lambda: p.work(
            db_session, shop, starts_at=p.at(9, 30), ends_at=p.at(10, 30), resources=[shop.lift_1, shop.max],
            vehicle=golf, for_job=service,
        )
    )
    assert details == {
        "reason": "planning_person_twice_on_job",
        "bookingIds": [str(first.booking.id)],
        "resourceId": str(shop.max),
    }


def test_one_person_on_two_different_jobs_at_once_is_allowed(db_session, shop):
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.max])
    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_2, shop.max])
    assert _count(db_session, Booking) == 2


# --- check 18 -------------------------------------------------------------------------------


def test_outside_working_hours_warns_and_writes_nothing(db_session, shop):
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(6, 30), ends_at=p.at(7, 30), resources=[shop.lift_1, shop.lea]),
        UnprocessableEntityError,
    )
    assert details == {
        "reason": "planning_warnings_unconfirmed",
        "codes": ["outside_working_hours"],
        "unconfirmed": ["outside_working_hours"],
        "warnings": [{"code": "outside_working_hours", "resourceId": str(shop.lea), "againstBookingId": None}],
    }
    assert (_count(db_session, Booking), _count(db_session, OverruledWarning), _events(db_session)) == (0, 0, [])


def test_a_booking_made_against_the_warning_keeps_the_code_the_name_and_the_time(db_session, shop):
    outcome = p.work(
        db_session, shop, starts_at=p.at(6, 30), ends_at=p.at(7, 30), resources=[shop.lift_1, shop.lea],
        confirmed=[W.OUTSIDE_WORKING_HOURS],
    )
    (kept,) = outcome.booking.overruled_warnings
    assert (kept.code, kept.resource_id, kept.change, kept.overruled_by, kept.overruled_by_name) == (
        W.OUTSIDE_WORKING_HOURS, shop.lea, planning.BookingChange.BOOKED, shop.actor_id, "Anna Chef",
    )
    assert (kept.booked_starts_at, kept.booked_ends_at) == (p.at(6, 30), p.at(7, 30))
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == outcome.booking.id, AuditEvent.action == "warning_overruled")
    ).all()
    assert len(audit) == 1 and audit[0].actor_id == shop.actor_id
    assert (audit[0].after["code"], audit[0].after["overruledByName"], audit[0].after["resourceId"]) == (
        "outside_working_hours", "Anna Chef", str(shop.lea),
    )
    (booked,) = _events(db_session, "planning.booking.booked")
    assert booked.payload["overruledWarnings"] == ["outside_working_hours"]
    # Read again: the record carries what was overruled.
    (again,) = planning.bookings_of_reference(
        db_session, dealership_id=shop.dealership_id, reference_id=outcome.booking.reference.id
    )
    assert again.overruled_warnings == outcome.booking.overruled_warnings


def test_the_lunch_break_is_outside_and_the_afternoon_inside(db_session, shop):
    _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(11, 45), ends_at=p.at(12, 15), resources=[shop.lift_1, shop.lea]),
        UnprocessableEntityError,
    )
    p.work(db_session, shop, starts_at=p.at(13), ends_at=p.at(17), resources=[shop.lift_1, shop.lea])


def test_a_person_without_working_hours_gets_no_warning(db_session, shop):
    assert _count(db_session, WorkingHours) == 5  # Lea's week; Max has none
    p.work(db_session, shop, starts_at=p.at(5), ends_at=p.at(6), resources=[shop.lift_1, shop.max])


def test_a_weekday_without_hours_is_outside_them(db_session, shop):
    saturday = p.MONDAY + dt.timedelta(days=5)
    details = _refused(
        lambda: p.work(
            db_session, shop, starts_at=p.at(9, day=saturday), ends_at=p.at(10, day=saturday),
            resources=[shop.lift_1, shop.lea],
        ),
        UnprocessableEntityError,
    )
    assert details["codes"] == ["outside_working_hours"]


def test_confirming_another_code_does_not_overrule(db_session, shop):
    details = _refused(
        lambda: p.work(
            db_session, shop, starts_at=p.at(6, 30), ends_at=p.at(7, 30), resources=[shop.lift_1, shop.lea],
            confirmed=[W.RESOURCE_BLOCKED],
        ),
        UnprocessableEntityError,
    )
    assert details["unconfirmed"] == ["outside_working_hours"]
    assert _count(db_session, Booking) == 0


def test_working_hours_are_split_per_local_day_on_a_daylight_saving_day():
    """2027-03-28 is 23 hours long in Zurich: 08:00 local is 06:00 UTC that day."""

    sunday = dt.date(2027, 3, 28)
    week = [WorkingHours(weekday=7, starts_at=dt.time(8), ends_at=dt.time(12))]
    utc = dt.UTC
    assert not outside_working_hours(week, dt.datetime(2027, 3, 28, 6, tzinfo=utc), dt.datetime(2027, 3, 28, 10, tzinfo=utc))
    # 07:00 UTC the day before is 08:00 local (winter time): inside; but Saturday has no hours.
    assert outside_working_hours(week, dt.datetime(2027, 3, 27, 7, tzinfo=utc), dt.datetime(2027, 3, 27, 8, tzinfo=utc))
    # Across midnight into Monday, which has no hours either.
    assert outside_working_hours(week, p.at(11, day=sunday), p.at(1, day=sunday + dt.timedelta(days=1)))
    # Before the change 08:00 local was 07:00 UTC; on the day it is 06:00 UTC, so 05:30 UTC is outside.
    assert outside_working_hours(week, dt.datetime(2027, 3, 28, 5, 30, tzinfo=utc), dt.datetime(2027, 3, 28, 7, tzinfo=utc))
    assert not outside_working_hours([], p.at(0, day=sunday), p.at(23, day=sunday))


# --- drop-off and pick-up -------------------------------------------------------------------


def test_work_before_the_drop_off_and_after_the_pick_up_warns(db_session, shop):
    order = p.order()
    drop_off = p.slot(db_session, shop, planning.BookingKind.DROP_OFF, starts_at=p.at(8), reference=order, resources=[shop.max])
    pick_up = p.slot(db_session, shop, planning.BookingKind.PICK_UP, starts_at=p.at(16), reference=order, resources=[shop.max])
    assert (drop_off.booking.starts_at, drop_off.booking.ends_at) == (p.at(8), p.at(8, 30))  # O7: 30 minutes

    p.work(db_session, shop, starts_at=p.at(8, 30), ends_at=p.at(16), resources=[shop.lift_1], reference=order)
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(8, 15), ends_at=p.at(16, 15), resources=[shop.lift_2], reference=order),
        UnprocessableEntityError,
    )
    assert details["codes"] == ["after_pick_up", "before_drop_off"]
    assert {(w["code"], w["againstBookingId"]) for w in details["warnings"]} == {
        ("before_drop_off", str(drop_off.booking.id)),
        ("after_pick_up", str(pick_up.booking.id)),
    }
    booked = p.work(
        db_session, shop, starts_at=p.at(8, 15), ends_at=p.at(16, 15), resources=[shop.lift_2], reference=order,
        confirmed=[W.BEFORE_DROP_OFF, W.AFTER_PICK_UP],
    )
    assert sorted(w.code.value for w in booked.booking.overruled_warnings) == ["after_pick_up", "before_drop_off"]


def test_a_moved_slot_marks_the_work_it_newly_leaves_outside(db_session, shop):
    order = p.order()
    drop_off = p.slot(db_session, shop, planning.BookingKind.DROP_OFF, starts_at=p.at(8), reference=order, resources=[shop.max])
    early = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], reference=order)
    late = p.work(db_session, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_2], reference=order)
    moved = planning.move(
        db_session,
        dealership_id=shop.dealership_id,
        actor_id=shop.actor_id,
        booking_id=drop_off.booking.id,
        expected_version=1,
        starts_at=p.at(9, 30),
        idempotency_key=shop.keys(),
    )
    assert moved.booking.ends_at == p.at(10)  # a slot stays O7 long
    (marked,) = moved.marked_for_replanning
    assert (marked.id, marked.replan.cause, marked.replan.caused_by_booking_id, marked.version) == (
        early.booking.id, planning.ReplanCause.OUTSIDE_DROP_OFF_PICK_UP, drop_off.booking.id, 2,
    )
    assert [e.aggregate_id for e in _events(db_session, "planning.booking.marked_for_replanning")] == [early.booking.id]
    assert db_session.get(Booking, late.booking.id).replan_cause is None


# --- blocks ---------------------------------------------------------------------------------


def test_a_block_is_always_possible_and_marks_each_booking_it_hits(db_session, shop):
    hit = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.max])
    elsewhere = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_2])
    later = p.work(db_session, shop, starts_at=p.at(10), ends_at=p.at(11), resources=[shop.lift_1])
    # Off the grid, no reference: a block reads no setting and judges nothing.
    outcome = p.block(
        db_session, shop, starts_at=p.at(9, 7), ends_at=p.at(9, 53), resources=[shop.lift_1], note="  Hebebühne defekt "
    )
    assert (outcome.booking.kind, outcome.booking.note, outcome.booking.reference) == (
        planning.BookingKind.BLOCK, "Hebebühne defekt", None,
    )
    (marked,) = outcome.marked_for_replanning
    assert (marked.id, marked.replan.cause, marked.replan.caused_by_booking_id) == (
        hit.booking.id, planning.ReplanCause.BLOCKED, outcome.booking.id,
    )
    assert [e.aggregate_id for e in _events(db_session, "planning.booking.marked_for_replanning")] == [hit.booking.id]
    for untouched in (elsewhere, later):
        assert db_session.get(Booking, untouched.booking.id).replan_cause is None

    # A move clears the mark.
    moved = planning.move(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=hit.booking.id,
        expected_version=marked.version, starts_at=p.at(13), idempotency_key=shop.keys(),
    )
    assert (moved.booking.replan, moved.booking.ends_at, moved.booking.version) == (None, p.at(14), 3)


def test_a_block_note_is_at_most_200_characters(db_session, shop):
    details = _refused(
        lambda: p.block(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], note="x" * 201),
        UnprocessableEntityError,
    )
    assert details == {"reason": "planning_note_too_long", "maxLength": 200}


def test_booking_onto_a_blocked_resource_warns_and_is_kept_when_confirmed(db_session, shop):
    blocked = p.block(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.max])
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(9, 30), ends_at=p.at(10, 30), resources=[shop.lift_1, shop.max]),
        UnprocessableEntityError,
    )
    assert details["codes"] == ["resource_blocked"]
    assert {(w["resourceId"], w["againstBookingId"]) for w in details["warnings"]} == {
        (str(shop.lift_1), str(blocked.booking.id)),
        (str(shop.max), str(blocked.booking.id)),
    }
    outcome = p.work(
        db_session, shop, starts_at=p.at(9, 30), ends_at=p.at(10, 30), resources=[shop.lift_1, shop.max],
        confirmed=[W.RESOURCE_BLOCKED],
    )
    assert sorted((w.code, w.resource_id, w.against_booking_id) for w in outcome.booking.overruled_warnings) == sorted(
        [(W.RESOURCE_BLOCKED, shop.lift_1, blocked.booking.id), (W.RESOURCE_BLOCKED, shop.max, blocked.booking.id)]
    )
    assert outcome.booking.replan is None  # booked knowingly: not marked
    audit = db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.entity_id == outcome.booking.id, AuditEvent.action == "warning_overruled"
        )
    )
    assert audit == 2


# --- the booking's shape and place ----------------------------------------------------------


def test_the_booking_shape_is_checked(client, db_session, shop):
    def reason(call, error=UnprocessableEntityError):
        return _refused(call, error).get("reason")

    assert reason(lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lea])) == (
        "planning_work_needs_one_workplace"
    )
    assert reason(
        lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.lift_2])
    ) == "planning_work_needs_one_workplace"
    assert reason(
        lambda: p.slot(db_session, shop, planning.BookingKind.PICK_UP, starts_at=p.at(9), reference=p.order(), resources=[])
    ) == "planning_booking_needs_a_resource"
    assert reason(lambda: p.block(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[])) == (
        "planning_booking_needs_a_resource"
    )
    assert reason(lambda: p.work(db_session, shop, starts_at=p.at(10), ends_at=p.at(9), resources=[shop.lift_1])) == (
        "planning_interval_invalid"
    )
    naive = dt.datetime(2027, 3, 1, 9)  # noqa: DTZ001 — the refusal of a naive time is what is tested
    assert reason(lambda: p.work(db_session, shop, starts_at=naive, ends_at=p.at(10), resources=[shop.lift_1])) == (
        "planning_interval_invalid"
    )
    # Another Betrieb's resource is not found.
    other = p.workshop(client, db_session)
    with pytest.raises(NotFoundError):
        p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[other.lift_1])
    assert _count(db_session, Booking) == 0


def test_a_booking_needs_its_resources_at_one_active_location(client, db_session, shop):
    branch = Location(tenant_id=shop.dealership_id, code="WIN", name="Filiale Winterthur", is_active=True)
    db_session.add(branch)
    db_session.commit()
    there = resource_service.create_workplace(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, name="Bühne W", location_id=branch.id
    )
    db_session.commit()
    details = _refused(
        lambda: p.block(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, there.id]),
        UnprocessableEntityError,
    )
    assert details["reason"] == "planning_resources_at_several_locations"

    branch.is_active = False
    db_session.commit()
    with pytest.raises(UnprocessableEntityError) as raised:
        p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[there.id])
    assert raised.value.details == {"reason": "planning_location_inactive", "locationId": str(branch.id)}
    assert raised.value.message == "A deactivated location takes no planner resource."


# --- deactivating a resource ----------------------------------------------------------------


def test_deactivating_a_resource_marks_its_bookings_to_come(db_session, shop):
    past_monday = dt.date(2026, 1, 5)
    past = p.work(
        db_session, shop, starts_at=p.at(9, day=past_monday), ends_at=p.at(10, day=past_monday), resources=[shop.lift_1]
    )
    coming = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    other = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_2])
    p.block(db_session, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_1])
    lift = resource_service.get_resource_or_404(db_session, dealership_id=shop.dealership_id, resource_id=shop.lift_1)

    resource_service.change_resource(
        db_session, dealership_id=shop.dealership_id, resource_id=shop.lift_1, expected_version=lift.version,
        actor_id=shop.actor_id, changes={"is_active": False},
    )
    db_session.commit()

    marked = db_session.get(Booking, coming.booking.id)
    assert (marked.replan_cause, marked.replan_caused_by_resource_id, marked.version) == (
        planning.ReplanCause.RESOURCE_DEACTIVATED, shop.lift_1, 2,
    )
    assert db_session.get(Booking, past.booking.id).replan_cause is None
    assert db_session.get(Booking, other.booking.id).replan_cause is None
    (event,) = _events(db_session, "planning.booking.marked_for_replanning")
    assert (event.aggregate_id, event.payload["replan"]["cause"], event.payload["replan"]["causedByResourceId"]) == (
        coming.booking.id, "resource_deactivated", str(shop.lift_1),
    )
    # A deactivated resource cannot be booked again.
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(14), ends_at=p.at(15), resources=[shop.lift_1]),
        UnprocessableEntityError,
    )
    assert details == {"reason": "planning_resource_inactive", "resourceIds": [str(shop.lift_1)]}


# --- move, versions, idempotency ------------------------------------------------------------


def test_a_move_is_judged_against_every_booking_but_itself(db_session, shop):
    first = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    p.work(db_session, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_2])
    moved = planning.move(
        db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=first.booking.id,
        expected_version=1, starts_at=p.at(9, 30), idempotency_key=shop.keys(),
    )
    assert (moved.booking.starts_at, moved.booking.ends_at) == (p.at(9, 30), p.at(10, 30))
    details = _refused(
        lambda: planning.move(
            db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=first.booking.id,
            expected_version=2, starts_at=p.at(11), resource_ids=[shop.lift_2], idempotency_key=shop.keys(),
        )
    )
    assert details["reason"] == "planning_workplace_taken"
    stale = _refused(
        lambda: planning.move(
            db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=first.booking.id,
            expected_version=1, starts_at=p.at(13), idempotency_key=shop.keys(),
        )
    )
    assert stale == {"currentVersion": 2, "ifMatchVersion": 1}
    (event,) = _events(db_session, "planning.booking.moved")
    assert (event.payload["startsAt"], event.payload["version"]) == (p.at(9, 30).astimezone(dt.UTC).isoformat(), 2)


def test_a_key_seen_before_replays_and_writes_nothing_again(db_session, shop):
    key = shop.keys()
    golf, service, order = p.car(), p.job(), p.order()

    def once():
        return p.work(
            db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1], vehicle=golf,
            for_job=service, reference=order, key=key,
        )

    first, again = once(), once()
    assert again.booking.id == first.booking.id
    assert (_count(db_session, Booking), len(_events(db_session))) == (1, 1)
    details = _refused(
        lambda: p.work(db_session, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_1], key=key)
    )
    assert details == {"idempotencyKey": key}


def test_a_refused_write_holds_no_lock_after_the_call(engine, db_session, shop):
    """The refusal rolled its transaction back: a second session books at once."""

    from sqlalchemy.orm import sessionmaker

    p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    _refused(lambda: p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]))
    assert not db_session.in_transaction()
    other = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        p.work(other, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_1])
    finally:
        other.close()


def test_planning_public_hands_out_no_mapped_class(db_session, shop):
    outcome = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    for value in (outcome, outcome.booking, *outcome.booking.resources, outcome.booking.reference):
        assert not hasattr(value, "__table__") and not hasattr(value, "_sa_instance_state")


def test_another_betriebs_booking_is_not_found(client, db_session, shop):
    other_id = uuid.UUID(create_dealership(client))
    booked = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1])
    with pytest.raises(NotFoundError):
        planning.release(
            db_session, dealership_id=other_id, actor_id=shop.actor_id, booking_id=booked.booking.id,
            expected_version=1, idempotency_key=shop.keys(),
        )
    assert planning.bookings_of_reference(
        db_session, dealership_id=other_id, reference_id=booked.booking.reference.id
    ) == []
