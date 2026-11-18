"""KAN-226 PR-1 — a location's and the publishing profile's facts for other
contexts (Dealer Administration PRD FR-A-35, D-A-32; rules 4–6).

What is pinned here:
- `platform.location.updated` for a change to a published master-data field,
  one per write, with the payload's one shape (ids, version, changedFields,
  the published facts as they now stand) and never the contact person, the
  lead e-mail, the letterhead or the bank-account override;
- `platform.location.schedule_changed` for opening hours, each closure write
  and the holiday calendar;
- `platform.publishing_profile.updated` for every write a channel publishes
  (profile fields, overrides, AGB, imagery), none for lead routing;
- a write that changes nothing published states nothing; a location's
  creation states nothing;
- the fact lands in the row's transaction: a failed commit leaves neither;
- the outbox row carries the request's correlation id (KAN-125).
"""

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.core.outbox_model import OutboxMessage
from app.main import app as fastapi_app
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.schemas.location import LocationUpdate
from app.platform.organisation.services import location as location_service
from tests.platform.test_dealership import _create_dealership
from tests.platform.test_kan204_locations import _create, _manager, _patch, seed_holiday_calendars

UPDATED = "platform.location.updated"
SCHEDULE = "platform.location.schedule_changed"
PROFILE = "platform.publishing_profile.updated"
_PRIVATE_KEYS = {"contactPerson", "leadEmail", "letterheadAddressOverride", "bankAccountOverrideId"}


def _events(db_session, event_type: str) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == event_type).order_by(OutboxMessage.created_at)
        ).all()
    )


def _all_location_facts(db_session) -> list[OutboxMessage]:
    return _events(db_session, UPDATED) + _events(db_session, SCHEDULE)


# --- platform.location.updated ---------------------------------------------------------------------


def test_creating_a_betrieb_and_a_location_states_no_location_fact(client, db_session):
    dealership = _create_dealership(client)
    _create(client, dealership["id"])
    assert _all_location_facts(db_session) == []


def test_a_published_field_change_states_one_fact_with_the_payload_shape(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = _patch(client, dealership["id"], location, {"name": "Zürich Nord Occasionen", "phone": "+41449999999"})
    assert response.status_code == 200, response.text
    saved = response.json()

    [event] = _events(db_session, UPDATED)
    assert _events(db_session, SCHEDULE) == []
    assert event.tenant_id == uuid.UUID(dealership["id"])
    assert event.producer == "platform"
    assert (event.aggregate_type, event.aggregate_id) == ("location", uuid.UUID(location["id"]))
    payload = event.payload
    assert set(payload) == {"dealershipId", "locationId", "version", "changedFields", "location"}
    assert payload["dealershipId"] == dealership["id"] and payload["locationId"] == location["id"]
    assert payload["version"] == saved["version"]
    assert payload["changedFields"] == ["name", "phone"]
    facts = payload["location"]
    assert facts["name"] == "Zürich Nord Occasionen" and facts["phone"] == "+41449999999"
    assert facts["code"] == "ZH-NORD" and facts["addressLocality"] == "Zürich" and facts["isActive"] is True
    assert facts["locationTypes"] == ["showroom", "workshop"]
    assert facts["geoLat"] == "47.414100"
    assert not _PRIVATE_KEYS & set(facts)


def test_a_change_to_internal_fields_only_states_nothing(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = _patch(
        client,
        dealership["id"],
        location,
        {"contactPerson": "Beat Beispiel", "leadEmail": "neu@garage.ch", "letterheadAddressOverride": "Garage\nNord"},
    )
    assert response.status_code == 200, response.text
    assert _all_location_facts(db_session) == []


def test_a_write_that_changes_nothing_states_nothing(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = _patch(client, dealership["id"], location, {"name": location["name"], "phone": location["phone"]})
    assert response.status_code == 200, response.text
    assert _all_location_facts(db_session) == []


def test_deactivating_a_location_is_a_published_fact(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = _patch(client, dealership["id"], location, {"isActive": False})
    assert response.status_code == 200, response.text
    [event] = _events(db_session, UPDATED)
    assert event.payload["changedFields"] == ["isActive"]
    assert event.payload["location"]["isActive"] is False


def test_the_holiday_calendar_is_a_schedule_fact_and_both_land_from_one_write(client, db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = _patch(
        client, dealership["id"], location, {"holidayCalendarId": str(calendars["BE"].id), "email": "x@garage.ch"}
    )
    assert response.status_code == 200, response.text
    [updated] = _events(db_session, UPDATED)
    [schedule] = _events(db_session, SCHEDULE)
    assert updated.payload["changedFields"] == ["email"]
    assert schedule.payload["changedFields"] == ["holidayCalendarId"]
    # One entity, one payload shape.
    assert set(schedule.payload) == set(updated.payload)
    assert schedule.payload["location"] == updated.payload["location"]
    assert schedule.payload["location"]["holidayCalendarId"] == str(calendars["BE"].id)


def test_the_fact_lands_in_the_rows_transaction(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    row = db_session.get(Location, uuid.UUID(location["id"]))
    assert row is not None

    def failing_commit():
        db_session.rollback()
        raise RuntimeError("the commit failed")

    original = db_session.commit
    db_session.commit = failing_commit  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            location_service.update_location(
                db_session, location=row, data=LocationUpdate(name="Nie gespeichert"), actor_id=uuid.uuid4()
            )
    finally:
        db_session.commit = original  # type: ignore[method-assign]
    assert _all_location_facts(db_session) == []
    db_session.refresh(row)
    assert row.name == location["name"]


def test_the_fact_carries_the_requests_correlation_id(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    correlation = str(uuid.uuid4())
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}/locations/{location['id']}",
        json={"name": "Zürich Süd"},
        headers={**_manager(dealership["id"]), "If-Match": str(location["version"]), "X-Correlation-Id": correlation},
    )
    assert response.status_code == 200, response.text
    [event] = _events(db_session, UPDATED)
    assert str(event.correlation_id) == correlation


# --- platform.location.schedule_changed -------------------------------------------------------------


def _put_hours(client, dealership_id: str, location_id: str, version: int, hours: list[dict]):
    return client.put(
        f"/v1/dealerships/{dealership_id}/locations/{location_id}/opening-hours",
        json={"perDepartment": False, "hours": hours},
        headers={**_manager(dealership_id), "If-Match": str(version)},
    )


def test_opening_hours_state_one_schedule_fact_and_the_same_week_again_none(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    week = [{"weekday": day, "opensAt": "08:00", "closesAt": "18:00"} for day in range(1, 6)]
    first = _put_hours(client, dealership["id"], location["id"], location["version"], week)
    assert first.status_code == 200, first.text
    [event] = _events(db_session, SCHEDULE)
    assert event.payload["changedFields"] == ["openingHours"]
    assert event.payload["version"] == first.json()["locationVersion"]
    assert _events(db_session, UPDATED) == []

    again = _put_hours(client, dealership["id"], location["id"], first.json()["locationVersion"], week)
    assert again.status_code == 200, again.text
    assert len(_events(db_session, SCHEDULE)) == 1


def test_each_closure_write_states_a_schedule_fact(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    url = f"/v1/dealerships/{dealership['id']}/locations/{location['id']}/closures"
    created = client.post(
        url, json={"startsOn": "2026-07-20", "endsOn": "2026-08-07", "reason": "Betriebsferien"}, headers=_manager(dealership["id"])
    )
    assert created.status_code == 201, created.text
    closure = created.json()
    item = f"{url}/{closure['id']}"
    unchanged = client.patch(
        item, json={"reason": "Betriebsferien"}, headers={**_manager(dealership["id"]), "If-Match": str(closure["version"])}
    )
    assert unchanged.status_code == 200, unchanged.text
    assert len(_events(db_session, SCHEDULE)) == 1  # nothing changed, nothing stated
    changed = client.patch(
        item, json={"endsOn": "2026-08-14"}, headers={**_manager(dealership["id"]), "If-Match": str(unchanged.json()["version"])}
    )
    assert changed.status_code == 200, changed.text
    removed = client.delete(item, headers={**_manager(dealership["id"]), "If-Match": str(changed.json()["version"])})
    assert removed.status_code == 204, removed.text

    events = _events(db_session, SCHEDULE)
    assert [e.payload["changedFields"] for e in events] == [["closures"]] * 3
    assert {e.payload["locationId"] for e in events} == {location["id"]}
    assert _events(db_session, UPDATED) == []


# --- platform.publishing_profile.updated --------------------------------------------------------------


@pytest.fixture()
def object_store(tmp_path: Path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    try:
        yield store
    finally:
        fastapi_app.dependency_overrides.pop(get_object_store, None)


def _jpeg(width: int = 1200, height: int = 800) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _profile_url(dealership_id: str, suffix: str = "") -> str:
    return f"/v1/dealerships/{dealership_id}/publishing-profile{suffix}"


def _headers(dealership_id: str, version: int) -> dict[str, str]:
    return {**_manager(dealership_id), "If-Match": str(version)}


def _seat_id(client, dealership_id: str) -> str:
    return client.get(f"/v1/dealerships/{dealership_id}/locations", headers=_manager(dealership_id)).json()["items"][0]["id"]


def test_a_profile_field_change_states_one_fact_and_no_change_none(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    response = client.patch(
        _profile_url(dealership_id), json={"shortDescription": {"de": "Ihr Garagist"}, "isPublished": True}, headers=_headers(dealership_id, 1)
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    [event] = _events(db_session, PROFILE)
    assert event.tenant_id == uuid.UUID(dealership_id) and event.aggregate_type == "publishing_profile"
    assert event.payload == {
        "dealershipId": dealership_id,
        "profileId": str(event.aggregate_id),
        "version": saved["version"],
        "locationId": None,
        "changedFields": ["shortDescription", "isPublished"],
    }

    same = client.patch(
        _profile_url(dealership_id), json={"isPublished": True}, headers=_headers(dealership_id, saved["version"])
    )
    assert same.status_code == 200, same.text
    assert len(_events(db_session, PROFILE)) == 1


def test_a_location_override_states_its_site(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat_id(client, dealership_id)
    created = client.post(
        _profile_url(dealership_id, f"/locations/{seat}"), json={"deliveryFeeChf": "150.00"}, headers=_headers(dealership_id, 1)
    )
    assert created.status_code == 201, created.text
    version = created.json()["version"]
    updated = client.patch(
        _profile_url(dealership_id, f"/locations/{seat}"),
        json={"deliveryFeeChf": "180.00"},
        headers=_headers(dealership_id, version),
    )
    assert updated.status_code == 200, updated.text
    removed = client.delete(_profile_url(dealership_id, f"/locations/{seat}"), headers=_headers(dealership_id, updated.json()["version"]))
    assert removed.status_code == 200, removed.text

    events = _events(db_session, PROFILE)
    assert [e.payload["changedFields"] for e in events] == [["locationOverride"], ["deliveryFeeChf"], ["locationOverride"]]
    assert {e.payload["locationId"] for e in events} == {seat}
    assert [e.payload["version"] for e in events] == [version, updated.json()["version"], removed.json()["version"]]


def test_the_agb_and_the_imagery_state_facts(client, db_session, object_store):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat_id(client, dealership_id)
    pdf = b"%PDF-1.7\n% AGB\n%%EOF"
    saved = client.put(
        _profile_url(dealership_id, "/agb"), content=pdf, headers={**_headers(dealership_id, 1), "Content-Type": "application/pdf"}
    )
    assert saved.status_code == 200, saved.text
    removed = client.delete(_profile_url(dealership_id, "/agb"), headers=_headers(dealership_id, saved.json()["version"]))
    assert removed.status_code == 200, removed.text

    version = removed.json()["version"]
    ids = []
    for _ in range(2):
        added = client.post(
            _profile_url(dealership_id, f"/locations/{seat}/images/premises"),
            content=_jpeg(),
            headers={**_headers(dealership_id, version), "Content-Type": "image/jpeg"},
        )
        assert added.status_code == 201, added.text
        version = added.json()["version"]
        ids.append(added.json()["images"][-1]["id"])
    reordered = client.put(
        _profile_url(dealership_id, f"/locations/{seat}/images/premises/order"),
        json={"imageIds": list(reversed(ids))},
        headers=_headers(dealership_id, version),
    )
    assert reordered.status_code == 200, reordered.text
    primary = client.post(_profile_url(dealership_id, f"/images/{ids[1]}/primary"), headers=_headers(dealership_id, reordered.json()["version"]))
    assert primary.status_code == 200, primary.text
    gone = client.delete(_profile_url(dealership_id, f"/images/{ids[1]}"), headers=_headers(dealership_id, primary.json()["version"]))
    assert gone.status_code == 200, gone.text

    events = _events(db_session, PROFILE)
    assert [(e.payload["changedFields"], e.payload["locationId"]) for e in events] == [
        (["agbDocument"], None),
        (["agbDocument"], None),
        (["imagery"], seat),
        (["imagery"], seat),
        (["imagery"], seat),
        (["imagery"], seat),
        (["imagery"], seat),
    ]


def test_lead_routing_states_no_publishing_fact(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat_id(client, dealership_id)
    response = client.put(
        _profile_url(dealership_id, f"/locations/{seat}/lead-routing"),
        json={"leadEmail": "verkauf@garage-muster.ch", "createsTask": False},
        headers=_headers(dealership_id, 1),
    )
    assert response.status_code == 200, response.text
    assert _events(db_session, PROFILE) == []
    assert _all_location_facts(db_session) == []
