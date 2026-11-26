"""KAN-809: the planner page's API (slice 1, 3.8; PL1, PL2, PL4).

One day of one location — every booking not released that holds one of its
resources and overlaps the Europe/Zurich day, and the grid of O6 — and the
three writes the page makes: change a booking, release it, block resources.
Every rule is the one write path's: refusals 409 with `bookingIds`, warnings
422 until confirmed and then kept with the name. Behind the Aftersales module
(404) and the matrix row `planner` (ruling 180: aftersales and the manager
write; another role 403); the Betrieb's own data only (404)."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.permissions import ModuleCode
from app.planning import public as planning
from app.planning.models.booking import OverruledWarning
from app.planning.services.planner_day import day_window
from app.platform.organisation.models.dealership import Dealership, Location
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services.entitlement import list_active_modules
from tests import planning_support as p
from tests import setup_checklist_support as s
from tests.token_support import dealer_group_of


def _entitle(db, dealership_id) -> None:
    db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
    db.commit()


def _headers(client, db, dealership_id, *, roles=frozenset(), manager=False) -> dict[str, str]:
    user = s.manager(client, db, dealership_id, isDealerManager=manager)
    db.expire_all()
    dealership = db.get(Dealership, dealership_id)
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=dealership_id,
        group_id=dealer_group_of(dealership_id),
        roles=frozenset(roles),
        is_dealer_manager=manager,
        entitlements=list_active_modules(db, dealership_id),
        entitlements_version=dealership.entitlements_version,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def shop(client, db_session) -> p.Workshop:
    shop = p.workshop(client, db_session)
    _entitle(db_session, shop.dealership_id)
    return shop


@pytest.fixture()
def desk(client, db_session, shop) -> dict[str, str]:
    return _headers(client, db_session, shop.dealership_id, roles={AccessRole.AFTERSALES})


def _base(shop) -> str:
    return f"/v1/dealerships/{shop.dealership_id}/planning"


def _day(client, shop, headers, date=p.MONDAY, location_id=None):
    return client.get(
        f"{_base(shop)}/day", params={"locationId": str(location_id or shop.location_id), "date": date.isoformat()},
        headers=headers,
    )


def _iso(value: dt.datetime) -> str:
    return value.isoformat()


# --- the day ----------------------------------------------------------------------------------


def test_the_day_holds_the_locations_bookings_that_overlap_it_and_the_grid(client, db_session, shop, desk):
    late = p.work(db_session, shop, starts_at=p.at(23), ends_at=p.at(23, 30) + dt.timedelta(hours=1),
                  resources=[shop.lift_1, shop.max])
    nine = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_2])
    p.work(db_session, shop, starts_at=p.at(9, day=p.MONDAY + dt.timedelta(days=1)),
           ends_at=p.at(10, day=p.MONDAY + dt.timedelta(days=1)), resources=[shop.lift_1])
    gone = p.work(db_session, shop, starts_at=p.at(14), ends_at=p.at(15), resources=[shop.lift_1])
    planning.release(db_session, dealership_id=shop.dealership_id, actor_id=shop.actor_id, booking_id=gone.booking.id,
                     expected_version=gone.booking.version, idempotency_key=shop.keys())

    response = _day(client, shop, desk)

    assert response.status_code == 200, response.text
    body = response.json()
    start, end = day_window(p.MONDAY)
    assert (body["date"], body["gridMinutes"]) == ("2027-03-01", 15)
    assert (dt.datetime.fromisoformat(body["startsAt"]), dt.datetime.fromisoformat(body["endsAt"])) == (start, end)
    # By start; a booking running past midnight belongs to the day it overlaps; released and tomorrow's are not.
    assert [b["id"] for b in body["bookings"]] == [str(nine.booking.id), str(late.booking.id)]
    first = body["bookings"][1]
    assert [(r["resourceId"], r["kind"]) for r in first["resources"]] == [
        (str(shop.lift_1), "workplace"), (str(shop.max), "person"),
    ]
    assert first["reference"]["type"] == "aftersales.order" and first["job"] and first["vehicle"]
    assert first["kind"] == "work" and first["version"] == 1 and first["replan"] is None


def test_a_day_with_the_grid_not_set_says_so_and_another_location_is_empty(client, db_session, shop, desk):
    p.set_setting(db_session, shop.dealership_id, "planning.grid_minutes", None)
    other = Location(tenant_id=shop.dealership_id, code=f"L{uuid.uuid4().hex[:6]}", name="Filiale", is_active=True)
    db_session.add(other)
    db_session.commit()
    body = _day(client, shop, desk, location_id=other.id).json()
    assert (body["gridMinutes"], body["bookings"]) == (None, [])


def test_another_betriebs_location_and_a_location_that_does_not_exist_are_404(client, db_session, shop, desk):
    stranger = p.workshop(client, db_session)
    assert _day(client, shop, desk, location_id=stranger.location_id).status_code == 404
    assert _day(client, shop, desk, location_id=uuid.uuid4()).status_code == 404
    # Another Betrieb's route is 404 too (the tenant comes from the token).
    assert client.get(f"/v1/dealerships/{stranger.dealership_id}/planning/day",
                      params={"locationId": str(stranger.location_id), "date": "2027-03-01"},
                      headers=desk).status_code == 404


def test_without_the_module_everything_is_404_and_a_role_without_the_planner_is_403(client, db_session):
    plain = p.workshop(client, db_session)
    headers = _headers(client, db_session, plain.dealership_id, manager=True)
    assert _day(client, plain, headers).status_code == 404
    _entitle(db_session, plain.dealership_id)
    sales = _headers(client, db_session, plain.dealership_id, roles={AccessRole.SALES})
    assert _day(client, plain, sales).status_code == 403
    response = client.post(f"{_base(plain)}/blocks", json={
        "resourceIds": [str(plain.lift_1)], "startsAt": _iso(p.at(9)), "endsAt": _iso(p.at(10)),
    }, headers=sales)
    assert response.status_code == 403
    manager = _headers(client, db_session, plain.dealership_id, manager=True)
    assert _day(client, plain, manager).status_code == 200


# --- change -----------------------------------------------------------------------------------


def _move(client, shop, headers, booking: dict | planning.BookingRecord, **body):
    booking_id = booking["id"] if isinstance(booking, dict) else booking.id
    version = booking["version"] if isinstance(booking, dict) else booking.version
    return client.patch(f"{_base(shop)}/bookings/{booking_id}", json=body,
                        headers={**headers, "If-Match": str(version)})


def test_a_booking_is_moved_in_time_and_onto_other_resources(client, db_session, shop, desk):
    booked = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]).booking

    response = _move(client, shop, desk, booked, startsAt=_iso(p.at(10)), endsAt=_iso(p.at(11, 30)),
                     resourceIds=[str(shop.lift_2)])

    assert response.status_code == 200, response.text
    moved = response.json()["booking"]
    assert (dt.datetime.fromisoformat(moved["startsAt"]), dt.datetime.fromisoformat(moved["endsAt"])) == (
        p.at(10), p.at(11, 30),
    )
    assert [r["resourceId"] for r in moved["resources"]] == [str(shop.lift_2)]
    assert moved["version"] == 2
    stale = _move(client, shop, desk, booked, startsAt=_iso(p.at(12)))
    assert stale.status_code == 409


def test_a_refusal_names_its_reason_and_the_booking_in_the_way(client, db_session, shop, desk):
    taken = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]).booking
    other = p.work(db_session, shop, starts_at=p.at(11), ends_at=p.at(12), resources=[shop.lift_2]).booking

    response = _move(client, shop, desk, other, startsAt=_iso(p.at(9, 30)), resourceIds=[str(shop.lift_1)])

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {
        "reason": "planning_workplace_taken", "bookingIds": [str(taken.id)], "resourceId": str(shop.lift_1),
    }


def test_a_warning_refuses_until_confirmed_then_is_kept_with_the_name(client, db_session, shop, desk):
    booked = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1, shop.lea]).booking

    warned = _move(client, shop, desk, booked, startsAt=_iso(p.at(17, 30)), endsAt=_iso(p.at(18, 30)))
    assert warned.status_code == 422, warned.text
    details = warned.json()["error"]["details"]
    assert details["reason"] == "planning_warnings_unconfirmed"
    assert details["warnings"] == [{"code": "outside_working_hours", "resourceId": str(shop.lea), "againstBookingId": None}]

    confirmed = _move(client, shop, desk, booked, startsAt=_iso(p.at(17, 30)), endsAt=_iso(p.at(18, 30)),
                      confirmedWarnings=details["warnings"])

    assert confirmed.status_code == 200, confirmed.text
    [kept] = confirmed.json()["booking"]["overruledWarnings"]
    assert (kept["code"], kept["resourceId"], kept["change"]) == ("outside_working_hours", str(shop.lea), "moved")
    assert kept["overruledByName"]
    assert db_session.scalar(select(OverruledWarning.code).where(OverruledWarning.booking_id == booked.id)) == (
        planning.WarningCode.OUTSIDE_WORKING_HOURS
    )


# --- release and block ------------------------------------------------------------------------


def test_a_booking_is_released_and_leaves_the_day(client, db_session, shop, desk):
    booked = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]).booking
    path = f"{_base(shop)}/bookings/{booked.id}/release"

    assert client.post(path, headers={**desk, "If-Match": "7"}).status_code == 409
    response = client.post(path, headers={**desk, "If-Match": str(booked.version)})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "released"
    assert _day(client, shop, desk).json()["bookings"] == []


def test_a_block_is_made_with_an_optional_note_and_marks_what_it_hits(client, db_session, shop, desk):
    hit = p.work(db_session, shop, starts_at=p.at(9), ends_at=p.at(10), resources=[shop.lift_1]).booking
    path = f"{_base(shop)}/blocks"

    response = client.post(path, json={
        "resourceIds": [str(shop.lift_1), str(shop.max)], "startsAt": _iso(p.at(8, 7)), "endsAt": _iso(p.at(12)),
        "note": "Hebebühne in Wartung",
    }, headers={**desk, "Idempotency-Key": str(uuid.uuid4())})

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["booking"]["kind"], body["booking"]["note"]) == ("block", "Hebebühne in Wartung")
    [marked] = body["markedForReplanning"]
    assert (marked["id"], marked["replan"]["cause"], marked["replan"]["causedByBookingId"]) == (
        str(hit.id), "blocked", body["booking"]["id"],
    )
    # Without a note, too (Anto, 2026-11-25); a note over 200 characters is 422.
    bare = client.post(path, json={"resourceIds": [str(shop.lea)], "startsAt": _iso(p.at(13)),
                                   "endsAt": _iso(p.at(14))}, headers=desk)
    assert bare.status_code == 201, bare.text
    long = client.post(path, json={"resourceIds": [str(shop.lea)], "startsAt": _iso(p.at(13)),
                                   "endsAt": _iso(p.at(14)), "note": "x" * 201}, headers=desk)
    assert long.status_code == 422
    day = _day(client, shop, desk).json()
    assert {b["kind"] for b in day["bookings"]} == {"work", "block"}
    assert next(b for b in day["bookings"] if b["id"] == str(hit.id))["replan"]["cause"] == "blocked"
    assert db_session.scalar(
        select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(body["booking"]["id"]))
    )


def test_a_resource_of_another_betrieb_cannot_be_blocked(client, db_session, shop, desk):
    stranger = p.workshop(client, db_session)
    response = client.post(f"{_base(shop)}/blocks", json={
        "resourceIds": [str(stranger.lift_1)], "startsAt": _iso(p.at(9)), "endsAt": _iso(p.at(10)),
    }, headers=desk)
    assert response.status_code == 404, response.text
    # Rule 7: another Betrieb's resource reads as missing, and nothing was written.
    assert response.json()["error"]["code"] == "not_found"
    assert _day(client, shop, desk).json()["bookings"] == []


def test_a_retried_block_replays_its_answer_and_writes_one(client, db_session, shop, desk):
    headers = {**desk, "Idempotency-Key": str(uuid.uuid4())}
    body = {"resourceIds": [str(shop.lift_1)], "startsAt": _iso(p.at(9)), "endsAt": _iso(p.at(10))}

    first = client.post(f"{_base(shop)}/blocks", json=body, headers=headers)
    again = client.post(f"{_base(shop)}/blocks", json=body, headers=headers)

    assert (first.status_code, again.status_code) == (201, 201), again.text
    assert again.json()["booking"]["id"] == first.json()["booking"]["id"]
    assert len(_day(client, shop, desk).json()["bookings"]) == 1
