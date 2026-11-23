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

import pytest
from sqlalchemy import select

from app.core.relative_dates import today_in_session_zone
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding, GbpPushState
from app.integration.services import gbp_push
from app.platform.organisation.models.location_schedule import LocationClosure
from app.platform.public import closed_days_until
from tests.gbp_fakes import LOCATION_1, LOCATION_2
from tests.integration import test_kan226_business_profile as pr2
from tests.integration import test_kan226_business_profile_push as pr3
from tests.inventory.test_inventory_invoicing_gate import _make_dealership

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
    assert gbp_push.run_horizon_pushes(db_session, today=today) == {"pushed": 1, "unchanged": 0, "failed": 0}
    assert far not in _special_days(fake.patches()[-1])

    # The next night, nothing changed: nothing is sent.
    sent = len(fake.patches())
    assert gbp_push.run_horizon_pushes(db_session, today=today) == {"pushed": 0, "unchanged": 1, "failed": 0}
    assert len(fake.patches()) == sent

    # Ten days on the closure is inside the year: pushed, though no location fact was stated.
    later = today + dt.timedelta(days=10)
    assert gbp_push.run_horizon_pushes(db_session, today=later) == {"pushed": 1, "unchanged": 0, "failed": 0}
    assert far in _special_days(fake.patches()[-1])
    db_session.expire_all()
    state = db_session.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))
    assert state is not None and "specialHours" in state.pushed_fields


def test_only_active_bindings_are_pushed(db_session, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    _bind(db_session, google, dealership.id, location.id, status=GbpBindingStatus.PENDING)
    assert gbp_push.run_horizon_pushes(db_session) == {"pushed": 0, "unchanged": 0, "failed": 0}
    assert fake.patches() == []


def test_nothing_is_pushed_without_nexotecs_client(db_session, google, betrieb):
    fake, _ = google
    dealership, location = betrieb
    fake.track(LOCATION_1)
    _bind(db_session, google, dealership.id, location.id)
    assert gbp_push.run_horizon_pushes(db_session) == {"pushed": 0, "unchanged": 0, "failed": 0}
    assert fake.patches() == []


def test_one_failing_binding_never_stops_the_others_and_the_job_raises_to_be_retried(
    db_session, google, client_connection, monkeypatch
):
    fake, _ = google
    first, second = _make_dealership(db_session), _make_dealership(db_session)
    sites = [_site(db_session, first.id), _site(db_session, second.id)]
    for dealership, site, name in zip((first, second), sites, (LOCATION_1, LOCATION_2)):
        fake.track(name)
        _bind(db_session, google, dealership.id, site.id, google_location=name)
    failing = min(sites, key=lambda s: (s.tenant_id, s.id))  # the first one the job meets
    real_push = gbp_push._push

    def push(db, *, dealership_id, location_id, today=None):
        if location_id == failing.id:
            raise RuntimeError("Google unavailable")
        return real_push(db, dealership_id=dealership_id, location_id=location_id, today=today)

    monkeypatch.setattr(gbp_push, "_push", push)
    with pytest.raises(gbp_push.HorizonPushFailedError):
        gbp_push.run_horizon_pushes(db_session)
    db_session.expire_all()
    other = next(s for s in sites if s.id != failing.id)
    pushed = db_session.scalar(
        select(GbpPushState)
        .join(GbpLocationBinding, GbpLocationBinding.id == GbpPushState.binding_id)
        .where(GbpLocationBinding.location_id == other.id)
    )
    assert pushed is not None and pushed.source_hash is not None


def test_the_job_is_registered_before_the_drift_check():
    from app.core.daily_scheduler import registered_job_names
    from app.worker import register_daily_jobs

    register_daily_jobs()
    names = list(registered_job_names())
    assert names.index("integration.business_profile.horizon_push") < names.index(
        "integration.business_profile.drift_check"
    )
