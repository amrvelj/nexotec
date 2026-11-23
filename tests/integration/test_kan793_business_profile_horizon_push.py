"""KAN-793 — a closed day that enters the one-year horizon reaches Google
with no location event.

The push runs on location facts (`gbp_push.py`); its facts carry the closed
days from today for a year. A closure further away than a year is in no push,
and as the days pass it enters the window on a day nothing fires. The nightly
`integration.business_profile.horizon_push` offers every active binding to
the same push with today's facts: the debounce sends nothing when they are
unchanged, and the closure once it is inside the year.
"""

import datetime as dt
import json
import logging

import pytest
from sqlalchemy import select

from app.core.relative_dates import today_in_session_zone
from app.integration.adapters.google_business_profile import GoogleBusinessProfileAdapter
from app.integration.errors import ProviderTransportError
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding, GbpPushState
from app.integration.services import gbp_push
from app.platform.organisation.models.location_schedule import LocationClosure
from app.platform.public import closed_days_until
from tests.gbp_fakes import LOCATION_1, LOCATION_2
from tests.integration import test_kan226_business_profile as pr2
from tests.integration import test_kan226_business_profile_push as pr3
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.platform.test_kan204_locations import seed_holiday_calendars

google = pr2.google
client_connection = pr2.client_connection
_site, _bind = pr3._site, pr3._bind


@pytest.fixture
def betrieb(db_session):
    dealership = _make_dealership(db_session)
    return dealership, _site(db_session, dealership.id)


def _special_days(request) -> set[dt.date]:
    body = json.loads(request.content)
    periods = body.get("specialHours", {}).get("specialHourPeriods", [])
    return {dt.date(p["startDate"]["year"], p["startDate"]["month"], p["startDate"]["day"]) for p in periods}


def test_a_closure_entering_the_horizon_is_pushed_with_no_location_event(
    db_session, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    binding = _bind(db_session, google, dealership.id, location.id)
    today, _ = today_in_session_zone()
    far = closed_days_until(today) + dt.timedelta(days=10)  # beyond the year as of today
    db_session.add(LocationClosure(location_id=location.id, starts_on=far, ends_on=far, reason="Betriebsferien"))
    db_session.commit()

    # Tonight: the first push of the binding; the closure is not in it.
    assert gbp_push.run_horizon_pushes(db_session, today=today) == {
        "pushed": 1,
        "unchanged": 0,
        "grantRefused": 0,
        "failed": 0,
    }
    assert far not in _special_days(fake.patches()[-1])

    # The next night, nothing changed: nothing is sent.
    sent = len(fake.patches())
    assert gbp_push.run_horizon_pushes(db_session, today=today) == {
        "pushed": 0,
        "unchanged": 1,
        "grantRefused": 0,
        "failed": 0,
    }
    assert len(fake.patches()) == sent

    # Ten days on the closure is inside the year: pushed, though no location fact was stated.
    later = today + dt.timedelta(days=10)
    assert gbp_push.run_horizon_pushes(db_session, today=later) == {
        "pushed": 1,
        "unchanged": 0,
        "grantRefused": 0,
        "failed": 0,
    }
    assert far in _special_days(fake.patches()[-1])
    db_session.expire_all()
    state = db_session.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))
    assert state is not None and "specialHours" in state.pushed_fields


def test_a_calendar_holiday_entering_the_horizon_is_pushed_with_no_location_event(
    db_session, google, client_connection
):
    """The criterion's own case: a public holiday of the site's calendar (ZH,
    25 December), not a closure anyone entered."""

    fake, _ = google
    calendars = seed_holiday_calendars(db_session)
    dealership = _make_dealership(db_session)
    location = _site(db_session, dealership.id, holiday_calendar_id=calendars["ZH"].id)
    fake.track(LOCATION_1)
    _bind(db_session, google, dealership.id, location.id)
    christmas_next_year = dt.date(2027, 12, 25)

    eve = dt.date(2026, 12, 24)  # the horizon ends 24 December 2027
    assert gbp_push.run_horizon_pushes(db_session, today=eve)["pushed"] == 1
    assert christmas_next_year not in _special_days(fake.patches()[-1])

    christmas = dt.date(2026, 12, 25)  # the horizon now reaches 25 December 2027
    assert gbp_push.run_horizon_pushes(db_session, today=christmas)["pushed"] == 1
    assert christmas_next_year in _special_days(fake.patches()[-1])


def test_a_refused_grant_is_counted_and_moves_the_binding_to_error(db_session, google, client_connection, betrieb):
    fake, secrets_store = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    binding = _bind(db_session, google, dealership.id, location.id)
    fake.refused_refresh_tokens.add(secrets_store.values[(binding.id, "refresh_token")])
    assert gbp_push.run_horizon_pushes(db_session) == {"pushed": 0, "unchanged": 0, "grantRefused": 1, "failed": 0}
    db_session.expire_all()
    assert db_session.get(GbpLocationBinding, binding.id).status == GbpBindingStatus.ERROR
    assert fake.patches() == []


def test_a_failure_inside_the_push_rolls_back_that_location_only(
    db_session, google, client_connection, caplog, monkeypatch
):
    """Google failing the PATCH after the push state row was added: nothing
    of that location is kept, the other location is pushed, and the failure
    is a WARNING (the scheduler raises the ERROR once per episode, KAN-303)."""

    fake, _ = google
    first, second = _make_dealership(db_session), _make_dealership(db_session)
    failing, other = _site(db_session, first.id), _site(db_session, second.id)
    fake.track(LOCATION_1)
    fake.track(LOCATION_2)
    failing_binding = _bind(db_session, google, first.id, failing.id, google_location=LOCATION_1)
    other_binding = _bind(db_session, google, second.id, other.id, google_location=LOCATION_2)
    real_patch = GoogleBusinessProfileAdapter.patch_location

    def patch_location(self, *, location_name, **kwargs):
        if location_name == LOCATION_1:
            raise ProviderTransportError("Google unavailable")
        return real_patch(self, location_name=location_name, **kwargs)

    monkeypatch.setattr(GoogleBusinessProfileAdapter, "patch_location", patch_location)
    with (
        caplog.at_level(logging.WARNING, logger="app.integration.services.gbp_push"),
        pytest.raises(gbp_push.HorizonPushFailedError, match=str(failing.id)),
    ):
        gbp_push.run_horizon_pushes(db_session)
    db_session.expire_all()
    states = {row.binding_id: row for row in db_session.scalars(select(GbpPushState)).all()}
    assert failing_binding.id not in states
    assert states[other_binding.id].source_hash is not None
    mine = [r for r in caplog.records if r.name == "app.integration.services.gbp_push"]
    assert mine and all(r.levelno == logging.WARNING for r in mine)


def test_only_active_bindings_are_pushed(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    _bind(db_session, google, dealership.id, location.id, status=GbpBindingStatus.PENDING)
    assert gbp_push.run_horizon_pushes(db_session) == {"pushed": 0, "unchanged": 0, "grantRefused": 0, "failed": 0}
    assert fake.patches() == []


def test_nothing_is_pushed_without_nexotecs_client(db_session, google, betrieb):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    _bind(db_session, google, dealership.id, location.id)
    assert gbp_push.run_horizon_pushes(db_session) == {"pushed": 0, "unchanged": 0, "grantRefused": 0, "failed": 0}
    assert fake.patches() == []


def test_the_job_is_registered_before_the_drift_check():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()
    names = list(registered_job_names())
    assert names.index("integration.business_profile.horizon_push") < names.index(
        "integration.business_profile.drift_check"
    )
