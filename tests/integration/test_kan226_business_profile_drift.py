"""KAN-226 PR-4 (D-A-32, FR-A-35; ADR-199): the nightly Google Business
Profile drift check — each active binding's Google location read back and
compared, field by field, with what the push sends now. It reports, never
repairs.

What is pinned here:
- the comparison (`gbp_drift.drifted_fields`): a location read back after the
  push is in sync; Google's own formatting (a phone with blanks, a trailing
  `/`, a `{}` 00:00) is not drift; a changed phone, hours, more hours or an
  extra special day is drift by field code; a field Nexotec never pushed and
  holds no value for is the dealer's own and never reported; a day the
  closed-days horizon reached after the last push is not «missing» at Google;
- the job (`run_drift_checks`): one row per binding, replaced each night and
  never a PATCH; a deactivated location is skipped (ADR-199); a provider
  failure is recorded, never raised, and never stops the next binding; a
  refused grant moves the binding to error; nothing is read without
  Nexotec's client; the job is registered before the status notifications;
- the status page (`GbpDriftStatusProvider`): ok in sync, a warning naming
  the location on drift or a failed check, no item for a skipped check, a
  binding not active or another Betrieb's binding;
- the card's read (`GET …/business-profile/bindings`): the drift and the
  departments the push could not show; another Betrieb's is 404.

Against the recorded fixture only (`tests/gbp_fakes.py`, invented values):
nothing here has run against Google.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.relative_dates import today_in_session_zone
from app.core.status import StatusState
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpDriftCheck,
    GbpDriftOutcome,
    GbpLocationBinding,
    GbpPushState,
)
from app.integration.services import gbp_drift, gbp_push
from app.integration.services.gbp_status import DRIFT_SOURCE, GbpDriftStatusProvider
from app.platform.organisation.models.location_schedule import LocationClosure
from tests.gbp_fakes import LOCATION_1, LOCATION_2
from tests.identapi_support import make_user
from tests.integration import test_kan226_business_profile as pr2
from tests.integration import test_kan226_business_profile_push as pr3
from tests.inventory.test_inventory_invoicing_gate import _make_dealership

google = pr2.google
client_connection = pr2.client_connection
_facts, _hours, _site, _bind = pr3._facts, pr3._hours, pr3._site, pr3._bind
T = dt.time


@pytest.fixture
def betrieb(db_session):
    dealership = _make_dealership(db_session)
    return dealership, _site(db_session, dealership.id)


def _push(db_session, binding) -> None:
    assert gbp_push._push(db_session, dealership_id=binding.tenant_id, location_id=binding.location_id)
    db_session.commit()


def _bound_and_pushed(db_session, google, dealership, location, google_location=LOCATION_1):
    fake, _ = google
    fake.track(google_location)
    binding = _bind(db_session, google, dealership.id, location.id, google_location=google_location)
    _push(db_session, binding)
    return binding


def _check(db_session, binding) -> GbpDriftCheck | None:
    db_session.expire_all()
    return db_session.scalar(select(GbpDriftCheck).where(GbpDriftCheck.binding_id == binding.id))


def _run(db_session) -> dict[str, int]:
    return gbp_drift.run_drift_checks(db_session)


# -- the comparison (pure) ---------------------------------------------------------------


def _patch(**overrides):
    return gbp_push.build_patch(_facts(**overrides), offered_hours_types=frozenset({"SERVICE"}))


def _as_google(patch) -> dict:
    return {key: value for key, value in patch.fields.items() if value not in ({}, [])}


TODAY = dt.date(2026, 11, 22)


def test_what_the_push_sent_read_back_is_in_sync():
    patch = _patch()
    assert gbp_drift.drifted_fields(patch, _as_google(patch), today=TODAY, pushed_on=TODAY) == ()


def test_googles_own_formatting_is_not_drift():
    patch = _patch(website="garage-beispiel.invalid", phone="+41440000000")
    google = _as_google(patch)
    google["phoneNumbers"] = {"primaryPhone": "+41 44 000 00 00"}
    google["websiteUri"] = "https://Garage-Beispiel.invalid/"
    google["storefrontAddress"] = {**google["storefrontAddress"], "languageCode": "de", "locality": " zürich "}
    # A period without minutes at Google: the same time.
    first = google["regularHours"]["periods"][0]
    google["regularHours"]["periods"][0] = {**first, "openTime": {"hours": 8, "minutes": 0}}
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=TODAY) == ()


def test_a_changed_field_at_google_is_drift_by_its_code():
    patch = _patch(
        opening_hours=(
            _hours("sales", 1, second=None),
            _hours("service", 2, opens=T(7), closes=T(17), second=None),
        ),
        closed_days=(dt.date(2026, 12, 25),),
    )
    assert "moreHours" in patch.update_mask
    google = _as_google(patch)
    google["phoneNumbers"] = {"primaryPhone": "+41449999999"}
    google["regularHours"] = {"periods": google["regularHours"]["periods"][:0]}
    google["moreHours"] = [{"hoursTypeId": "SERVICE", "periods": []}]
    google["specialHours"] = {
        "specialHourPeriods": [
            *google["specialHours"]["specialHourPeriods"],
            {"startDate": {"year": 2026, "month": 12, "day": 31}, "closed": True},
        ]
    }
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=TODAY) == (
        "phone",
        "regularHours",
        "moreHours",
        "specialHours",
    )


def test_a_field_nexotec_never_pushes_is_the_dealers_own_and_never_reported():
    patch = _patch(website=None, phone=None)
    google = {**_as_google(patch), "websiteUri": "https://dealer-own.invalid", "phoneNumbers": {"primaryPhone": "+41"}}
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=TODAY) == ()


def test_a_field_being_cleared_is_drift_while_google_still_shows_it():
    patch = gbp_push.build_patch(
        _facts(phone=None), offered_hours_types=frozenset(), previously_pushed=frozenset({"phoneNumbers.primaryPhone"})
    )
    google = {**_as_google(patch), "phoneNumbers": {"primaryPhone": "+41440000000"}}
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=TODAY) == ("phone",)
    del google["phoneNumbers"]
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=TODAY) == ()


def test_a_day_the_horizon_reached_after_the_last_push_is_not_missing_at_google():
    pushed_on = TODAY - dt.timedelta(days=10)
    late = pushed_on + dt.timedelta(days=366)  # beyond what the last push could send
    patch = _patch(closed_days=(dt.date(2026, 12, 25), late))
    google = _as_google(patch)
    google["specialHours"] = {
        "specialHourPeriods": [
            # a past day the last push sent, and the one day both sides know
            {"startDate": {"year": 2026, "month": 11, "day": 15}, "closed": True},
            {"startDate": {"year": 2026, "month": 12, "day": 25}, "closed": True},
        ]
    }
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=pushed_on) == ()
    # A special opening at Google within the window is drift.
    google["specialHours"]["specialHourPeriods"].append(
        {"startDate": {"year": 2026, "month": 12, "day": 24}, "closed": False, "openTime": {"hours": 9}}
    )
    assert gbp_drift.drifted_fields(patch, google, today=TODAY, pushed_on=pushed_on) == ("specialHours",)


# -- the job -------------------------------------------------------------------------------


def test_a_pushed_location_read_back_is_in_sync_and_the_check_never_writes_to_google(
    db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    patches = len(fake.patches())

    assert _run(db_session)["in_sync"] == 1

    check = _check(db_session, binding)
    assert check.outcome == GbpDriftOutcome.IN_SYNC and check.drifted_fields == [] and check.reason is None
    assert len(fake.patches()) == patches
    [read] = [r for r in fake.requests if r.method == "GET" and "storefrontAddress" in r.url.params.get("readMask", "")]
    assert read.url.params["readMask"] == ",".join(gbp_drift.DRIFT_READ_MASK)


def test_a_change_at_google_is_drift_and_the_next_night_replaces_the_row(
    db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    fake.location_bodies[LOCATION_1]["phoneNumbers"] = {"primaryPhone": "+41 44 999 99 99"}
    fake.location_bodies[LOCATION_1]["specialHours"] = {
        "specialHourPeriods": [{"startDate": {"year": 2099, "month": 1, "day": 2}, "closed": True}]
    }

    _run(db_session)
    first = _check(db_session, binding)
    assert first.outcome == GbpDriftOutcome.DRIFT and first.drifted_fields == ["phone"]

    # Google's own edit reverted: the same row says in sync.
    fake.location_bodies[LOCATION_1]["phoneNumbers"] = {"primaryPhone": "+41440000000"}
    _run(db_session)
    rows = db_session.scalars(select(GbpDriftCheck).where(GbpDriftCheck.binding_id == binding.id)).all()
    assert len(rows) == 1 and rows[0].id == first.id and rows[0].outcome == GbpDriftOutcome.IN_SYNC


def test_a_location_whose_push_never_reached_google_is_drift(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    binding = _bind(db_session, google, dealership.id, location.id)

    _run(db_session)
    check = _check(db_session, binding)
    assert check.outcome == GbpDriftOutcome.DRIFT
    assert check.drifted_fields == ["address", "phone", "regularHours"]


def test_a_deactivated_location_is_skipped_not_drift(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    fake.location_bodies[LOCATION_1]["phoneNumbers"] = {"primaryPhone": "+41449999999"}
    location.is_active = False
    db_session.commit()
    requests = len(fake.requests)

    assert _run(db_session)["skipped"] == 1
    check = _check(db_session, binding)
    assert check.outcome == GbpDriftOutcome.SKIPPED and check.reason == gbp_drift.LOCATION_INACTIVE
    assert len(fake.requests) == requests  # Google is not even asked
    assert GbpDriftStatusProvider().items(db_session, dealership_id=dealership.id, now=utcnow()) == []


def test_a_failing_read_is_recorded_never_raised_and_the_next_binding_is_still_checked(
    db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    first = _bound_and_pushed(db_session, google, dealership, location)
    other = _make_dealership(db_session)
    second = _bound_and_pushed(db_session, google, other, _site(db_session, other.id), google_location=LOCATION_2)
    fake.get_failure_status = 503

    counts = _run(db_session)

    assert counts["failed"] == 2
    assert _check(db_session, first).reason == gbp_drift.PROVIDER_UNAVAILABLE
    fake.get_failure_status = 404
    _run(db_session)
    assert _check(db_session, first).reason == gbp_drift.PROVIDER_REFUSED
    fake.get_failure_status = None
    assert _run(db_session)["in_sync"] == 2
    assert _check(db_session, second).outcome == GbpDriftOutcome.IN_SYNC


def test_one_binding_raising_never_undoes_another(db_session, google, client_connection, betrieb, monkeypatch):
    dealership, location = betrieb
    first = _bound_and_pushed(db_session, google, dealership, location)
    other = _make_dealership(db_session)
    second = _bound_and_pushed(db_session, google, other, _site(db_session, other.id), google_location=LOCATION_2)
    real = gbp_drift.check_binding

    def flaky(db, binding, **kwargs):
        if binding.id == min(first.id, second.id):
            raise RuntimeError("a bug in one binding's data")
        return real(db, binding, **kwargs)

    monkeypatch.setattr(gbp_drift, "check_binding", flaky)
    assert _run(db_session)["in_sync"] == 1
    assert _check(db_session, max(first, second, key=lambda b: b.id)).outcome == GbpDriftOutcome.IN_SYNC
    assert _check(db_session, min(first, second, key=lambda b: b.id)) is None


def test_a_refused_grant_moves_the_binding_to_error(db_session, google, client_connection, betrieb):
    fake, secrets_store = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    fake.refused_refresh_tokens.add(secrets_store.values[(binding.id, "refresh_token")])
    fake.access_tokens.clear()

    _run(db_session)

    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, binding.id).status == GbpBindingStatus.ERROR
    assert _check(db_session, binding).reason == gbp_drift.GRANT_REFUSED
    # The next night the binding is no longer active: nothing is read for it.
    requests = len(fake.requests)
    assert sum(_run(db_session).values()) == 0 and len(fake.requests) == requests


def test_nothing_is_read_without_nexotecs_client(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    client_connection.enabled = False
    db_session.commit()
    requests = len(fake.requests)

    assert sum(_run(db_session).values()) == 0
    assert _check(db_session, binding) is None and len(fake.requests) == requests


def test_the_job_is_registered_before_the_status_notifications():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()
    names = list(registered_job_names())
    assert names.index("integration.business_profile.drift_check") < names.index("platform.status_page.notifications")


# -- the status page ------------------------------------------------------------------------


def _items(db_session, dealership_id):
    return GbpDriftStatusProvider().items(db_session, dealership_id=dealership_id, now=utcnow())


def test_the_status_item_follows_the_check_and_names_the_location(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    assert _items(db_session, dealership.id) == []  # never checked yet

    _run(db_session)
    [item] = _items(db_session, dealership.id)
    assert item.state == StatusState.OK and item.source == DRIFT_SOURCE
    assert item.key == f"google_business_profile.drift.{binding.id}" and len(item.key) <= 96
    assert item.message_key == "statusPage.items.googleBusinessProfileDrift.inSync"
    assert item.link == "/administration/integrations" and item.params == {"location": "Zürich"}

    fake.location_bodies[LOCATION_1]["regularHours"] = {"periods": []}
    fake.location_bodies[LOCATION_1]["phoneNumbers"] = {"primaryPhone": "+41449999999"}
    _run(db_session)
    [item] = _items(db_session, dealership.id)
    assert item.state == StatusState.WARNING
    assert item.message_key == "statusPage.items.googleBusinessProfileDrift.drift"
    assert item.params == {"location": "Zürich", "count": 2}

    fake.get_failure_status = 503
    _run(db_session)
    [item] = _items(db_session, dealership.id)
    assert item.state == StatusState.WARNING
    assert item.message_key == "statusPage.items.googleBusinessProfileDrift.checkFailed"


def test_no_item_for_a_binding_not_active_or_another_betriebs(db_session, google, client_connection, betrieb):
    dealership, location = betrieb
    binding = _bound_and_pushed(db_session, google, dealership, location)
    _run(db_session)
    other = _make_dealership(db_session)
    assert _items(db_session, other.id) == []

    binding.status = GbpBindingStatus.DISCONNECTED
    db_session.commit()
    assert _items(db_session, dealership.id) == []


def test_the_provider_is_registered_by_the_composition_root():
    from app.core.status import status_providers
    from app.status_registry import register_status_providers

    register_status_providers()
    assert DRIFT_SOURCE in {provider.source for provider in status_providers()}


# -- the card's read ------------------------------------------------------------------------


def test_the_bindings_list_carries_the_drift_and_the_unmapped_departments(
    client, db_session, google, client_connection, betrieb
):
    dealership, location = betrieb
    manager = make_user(db_session, tenant_id=dealership.id, email="chef-drift@example.test")
    binding = _bound_and_pushed(db_session, google, dealership, location)
    state = db_session.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))
    state.unmapped_departments = ["parts"]
    db_session.add(
        LocationClosure(
            location_id=location.id,
            starts_on=today_in_session_zone()[0] + dt.timedelta(days=3),
            ends_on=today_in_session_zone()[0] + dt.timedelta(days=3),
            reason="Inventur",
        )
    )
    db_session.commit()

    base = f"/v1/dealerships/{dealership.id}/business-profile/bindings"
    before = client.get(base, headers=pr2._as(manager))
    assert before.status_code == 200, before.text
    [row] = before.json()["items"]
    assert row["drift"] is None and row["unmappedDepartments"] == ["parts"]

    _run(db_session)
    [row] = client.get(base, headers=pr2._as(manager)).json()["items"]
    # The closure was never pushed: Google lacks it.
    assert row["drift"]["outcome"] == "drift" and row["drift"]["driftedFields"] == ["specialHours"]
    assert row["drift"]["reason"] is None and row["drift"]["checkedAt"]

    outsider_betrieb = _make_dealership(db_session)
    outsider = make_user(db_session, tenant_id=outsider_betrieb.id, email="chef-other@example.test")
    assert client.get(base, headers=pr2._as(outsider)).status_code == 404
