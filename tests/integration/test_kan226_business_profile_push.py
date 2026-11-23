"""KAN-226 PR-3 (D-A-32, FR-A-35; Anto's Q1 A): the Google Business Profile
push — Platform's location facts (and Integration's own fact that a binding
went live) push the location's fields, hours and closed days to its Google
location, one way. Photos are not pushed yet (KAN-784).

What is pinned here:
- the mapping (`gbp_push.build_patch`): the main department → regularHours,
  the other departments → moreHours under the type Google's category offers,
  one Google does not offer left out and recorded, two intervals, 00:00 as
  `{}`, closures and holidays → specialHours closed days, the address block,
  phone and website, a field never pushed and without a value never in the
  mask, a field pushed before and empty now masked and cleared;
- Platform's read (`platform.public.location_channel_facts`): tenant-scoped,
  closures and observed holidays within the horizon;
- the consumers: a redelivered event pushes nothing again; a burst is one
  push (the debounce); a transient failure raises (retried, then
  dead-lettered); a refused grant moves the binding to error and is not
  retried; no live binding, an inactive site or another Betrieb's location
  sends nothing; the activation pushes.

Against the recorded fixture only (`tests/gbp_fakes.py`, invented values):
nothing here has run against Google.
"""

import dataclasses
import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import DeliveryError, InProcessTransport
from app.core.outbox_worker import MAX_ATTEMPTS, poll_once
from app.core.relative_dates import today_in_session_zone
from app.integration.consumers import handle_business_profile_push_message
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpLocationBinding,
    GbpPushState,
)
from app.integration.services import gbp_link, gbp_push
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.location_schedule import (
    LocationClosure,
    LocationDepartment,
    LocationOpeningHours,
)
from app.platform.organisation.services import location_facts
from app.platform.public import ChannelOpeningHours, LocationChannelFacts, location_channel_facts
from tests.gbp_fakes import ACCOUNT_1, LOCATION_1, LOCATION_2
from tests.identapi_support import make_user
from tests.integration import test_kan226_business_profile as pr2
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.platform.test_kan204_locations import seed_holiday_calendars

# The PR-2 fixtures: the fake Google and Nexotec's client connection.
google = pr2.google
client_connection = pr2.client_connection
_link, _pick = pr2._link, pr2._pick

PUSH = "integration.business_profile_push_on_schedule_changed"
TODAY = dt.date(2026, 11, 21)
T = dt.time


def _hours(department: str, weekday: int, opens=T(8), closes=T(12), second=(T(13, 30), T(18))) -> ChannelOpeningHours:
    return ChannelOpeningHours(
        department=department,
        weekday=weekday,
        opens_at=opens,
        closes_at=closes,
        second_opens_at=second[0] if second else None,
        second_closes_at=second[1] if second else None,
    )


def _facts(**overrides) -> LocationChannelFacts:
    base: dict = dict(  # noqa: C408
        dealership_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        name="Zürich",
        is_active=True,
        address_street="Beispielstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        address_country="CH",
        phone="+41440000000",
        website="garage-beispiel.invalid",
        opening_hours=tuple(_hours(d, w) for d in ("sales", "service", "parts") for w in (1, 2)),
        closed_days=(),
    )
    base.update(overrides)
    return LocationChannelFacts(**base)


# -- the mapping -------------------------------------------------------------------------


def test_same_hours_for_every_department_are_regular_hours_only_with_two_intervals():
    patch = gbp_push.build_patch(_facts(), offered_hours_types=frozenset({"SERVICE"}))

    assert patch.update_mask == ("storefrontAddress", "phoneNumbers.primaryPhone", "websiteUri", "regularHours")
    periods = patch.fields["regularHours"]["periods"]
    assert periods[:2] == [
        {"openDay": "MONDAY", "openTime": {"hours": 8}, "closeDay": "MONDAY", "closeTime": {"hours": 12}},
        {
            "openDay": "MONDAY",
            "openTime": {"hours": 13, "minutes": 30},
            "closeDay": "MONDAY",
            "closeTime": {"hours": 18},
        },
    ]
    assert len(periods) == 4 and "moreHours" not in patch.fields and patch.unmapped_departments == ()


def test_other_departments_go_to_more_hours_under_an_offered_type_and_the_rest_is_recorded():
    hours = (
        _hours("sales", 6, opens=T(0), closes=T(16), second=None),
        _hours("service", 1, opens=T(7, 15), closes=T(17), second=None),
        _hours("parts", 1, opens=T(7), closes=T(11), second=None),
    )
    patch = gbp_push.build_patch(_facts(opening_hours=hours), offered_hours_types=frozenset({"SERVICE", "BUSINESS"}))

    # 00:00 is Google's zero `TimeOfDay`: `{}`.
    assert patch.fields["regularHours"]["periods"] == [
        {"openDay": "SATURDAY", "openTime": {}, "closeDay": "SATURDAY", "closeTime": {"hours": 16}}
    ]
    assert patch.fields["moreHours"] == [
        {
            "hoursTypeId": "SERVICE",
            "periods": [
                {
                    "openDay": "MONDAY",
                    "openTime": {"hours": 7, "minutes": 15},
                    "closeDay": "MONDAY",
                    "closeTime": {"hours": 17},
                }
            ],
        }
    ]
    assert "moreHours" in patch.update_mask
    assert patch.unmapped_departments == ("parts",)


def test_a_workshop_only_site_shows_its_service_hours_as_regular_hours():
    hours = (_hours("service", 3, second=None),)
    patch = gbp_push.build_patch(_facts(opening_hours=hours), offered_hours_types=frozenset())
    assert patch.fields["regularHours"]["periods"][0]["openDay"] == "WEDNESDAY"
    assert "moreHours" not in patch.fields and patch.unmapped_departments == ()


def test_a_field_without_a_value_is_never_in_the_mask_so_nothing_is_cleared_at_google():
    patch = gbp_push.build_patch(
        _facts(phone=" ", website=None, opening_hours=(), address_house_number=None, address_postal_code=None),
        offered_hours_types=frozenset(),
    )
    # The street is there but the postal code is not: an incomplete address is not sent.
    assert patch.update_mask == () and patch.fields == {}


def test_the_address_block_phone_and_website_are_mapped():
    patch = gbp_push.build_patch(
        _facts(address_country=None, website="https://garage-beispiel.invalid/kontakt", opening_hours=()),
        offered_hours_types=frozenset(),
    )
    assert patch.fields["storefrontAddress"] == {
        "regionCode": "CH",
        "postalCode": "8001",
        "locality": "Zürich",
        "administrativeArea": "ZH",
        "addressLines": ["Beispielstrasse 1"],
    }
    assert len(patch.fields["storefrontAddress"]["addressLines"]) <= gbp_push.MAX_ADDRESS_LINES
    assert patch.fields["phoneNumbers"] == {"primaryPhone": "+41440000000"}
    assert patch.fields["websiteUri"] == "https://garage-beispiel.invalid/kontakt"
    assert gbp_push.build_patch(_facts(), offered_hours_types=frozenset()).fields["websiteUri"] == (
        "https://garage-beispiel.invalid"
    )


def test_closed_days_are_closed_special_hours():
    patch = gbp_push.build_patch(
        _facts(closed_days=(dt.date(2026, 12, 25), dt.date(2026, 12, 26))), offered_hours_types=frozenset()
    )
    assert patch.fields["specialHours"] == {
        "specialHourPeriods": [
            {"startDate": {"year": 2026, "month": 12, "day": 25}, "closed": True},
            {"startDate": {"year": 2026, "month": 12, "day": 26}, "closed": True},
        ]
    }
    assert patch.update_mask[-1] == "specialHours"


def test_every_masked_path_is_one_the_adapter_lets_through():
    hours = (_hours("sales", 1, second=None), _hours("service", 2, second=None))
    patch = gbp_push.build_patch(
        _facts(opening_hours=hours, closed_days=(TODAY,)), offered_hours_types=frozenset({"SERVICE"})
    )
    from app.integration.adapters.google_business_profile import PATCHABLE_FIELDS

    assert set(patch.update_mask) <= PATCHABLE_FIELDS and len(patch.update_mask) == 6


# -- Platform's read ---------------------------------------------------------------------------


def _site(db_session, dealership_id, **overrides) -> Location:
    fields: dict = dict(  # noqa: C408
        tenant_id=dealership_id,
        name="Zürich",
        address_street="Beispielstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        address_country="CH",
        phone="+41440000000",
        contact_person="Intern Person",
        lead_email="leads@example.test",
        location_types=[],
    )
    fields.update(overrides)
    location = Location(**fields)
    db_session.add(location)
    db_session.flush()
    for weekday in (1, 2, 3, 4, 5):
        for department in LocationDepartment:
            db_session.add(
                LocationOpeningHours(
                    location_id=location.id, department=department, weekday=weekday, opens_at=T(8), closes_at=T(18)
                )
            )
    db_session.commit()
    return location


def test_the_read_lists_closures_and_observed_holidays_within_the_horizon_and_is_tenant_scoped(db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _make_dealership(db_session)
    location = _site(db_session, dealership.id, holiday_calendar_id=calendars["ZH"].id)
    db_session.add_all(
        [
            LocationClosure(location_id=location.id, starts_on=TODAY - dt.timedelta(days=1), ends_on=TODAY, reason="x"),
            LocationClosure(
                location_id=location.id, starts_on=dt.date(2025, 1, 1), ends_on=dt.date(2025, 1, 2), reason="y"
            ),
        ]
    )
    db_session.commit()

    facts = location_channel_facts(db_session, dealership_id=dealership.id, location_id=location.id, today=TODAY)

    assert facts is not None and facts.closed_days[0] == TODAY  # the past half of the closure is gone
    assert dt.date(2025, 1, 1) not in facts.closed_days
    assert (
        dt.date(2027, 8, 1) in facts.closed_days and dt.date(2027, 12, 25) not in facts.closed_days
    )  # 1 August, the horizon
    assert all(TODAY <= day <= TODAY + dt.timedelta(days=365) for day in facts.closed_days)
    assert len(facts.opening_hours) == 15 and facts.website == dealership.website
    assert not {"contact_person", "lead_email"} & set(LocationChannelFacts.__dataclass_fields__)
    other = _make_dealership(db_session)
    assert location_channel_facts(db_session, dealership_id=other.id, location_id=location.id, today=TODAY) is None


# -- the consumers ------------------------------------------------------------------------------


@pytest.fixture
def betrieb(db_session):
    dealership = _make_dealership(db_session)
    return dealership, _site(db_session, dealership.id)


def _bind(
    db_session, google, dealership_id, location_id, *, google_location=LOCATION_1, status=GbpBindingStatus.ACTIVE
):
    fake, secrets_store = google
    binding = GbpLocationBinding(
        tenant_id=dealership_id,
        location_id=location_id,
        location_label="Zürich",
        location_label_refreshed_at=utcnow(),
        status=status,
        google_account_name=ACCOUNT_1,
        google_location_name=google_location,
        connected_at=utcnow(),
    )
    db_session.add(binding)
    db_session.commit()
    refresh = f"1//invented-refresh-{uuid.uuid4().hex[:8]}"
    fake.refresh_tokens.add(refresh)
    secrets_store.values[(binding.id, "refresh_token")] = refresh
    return binding


def _schedule_fact(db_session, location: Location) -> OutboxMessage:
    location_facts.publish_schedule_changed(db_session, location=location, changed=location_facts.OPENING_HOURS)
    db_session.commit()
    return _latest(db_session, location_facts.LOCATION_SCHEDULE_CHANGED_EVENT)


def _latest(db_session, event_type: str) -> OutboxMessage:
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type).order_by(OutboxMessage.created_at.desc())
    ).first()


def _transport(engine, consumer_name=PUSH, handler=handle_business_profile_push_message, event_type=None):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    transport = InProcessTransport(factory)
    transport.register(
        event_type or location_facts.LOCATION_SCHEDULE_CHANGED_EVENT, consumer_name=consumer_name, handler=handler
    )
    return transport


def _state(db_session, binding) -> GbpPushState | None:
    db_session.expire_all()
    return db_session.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))


def test_a_schedule_fact_pushes_the_current_state_once_and_a_redelivery_sends_nothing(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    message = _schedule_fact(db_session, location)
    transport = _transport(engine)

    transport.deliver(message)
    transport.deliver(message)

    [patch] = fake.patches()
    assert patch.url.params["updateMask"] == "storefrontAddress,phoneNumbers.primaryPhone,regularHours"
    assert str(patch.url).split("?")[0].endswith(LOCATION_1)
    # Every department has the same hours: no category read is needed.
    assert not [r for r in fake.requests if "categories" in r.url.params.get("readMask", "")]
    state = _state(db_session, binding)
    assert state.pushed_fields == ["storefrontAddress", "phoneNumbers.primaryPhone", "regularHours"]
    assert state.source_hash and state.unmapped_departments == []
    assert b"Intern Person" not in patch.content and b"leads@example.test" not in patch.content


def test_a_state_with_nothing_to_send_asks_google_for_no_token(
    db_session, engine, google, client_connection, betrieb, monkeypatch
):
    """KAN-788: a changed state whose patch masks nothing (no complete address,
    no phone, website, hours or closed days, nothing pushed before) calls
    Google not at all — not even the token endpoint. The state is still
    recorded, so the next delivery is debounced."""

    fake, _ = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    real_facts = gbp_push._facts

    def bare(db, dealership_id, location_id, *, today=None):
        facts = real_facts(db, dealership_id, location_id, today=today)
        return dataclasses.replace(
            facts, address_street=None, address_house_number=None, phone=None, website=None, opening_hours=(), closed_days=()
        )

    monkeypatch.setattr(gbp_push, "_facts", bare)
    message = _schedule_fact(db_session, location)
    before = len(fake.requests)

    _transport(engine).deliver(message)

    assert fake.requests[before:] == []
    state = _state(db_session, binding)
    assert state.source_hash and state.pushed_fields == []


def test_a_burst_of_facts_is_one_push_and_a_real_change_pushes_again(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    _bind(db_session, google, dealership.id, location.id)
    transport = _transport(engine)
    first, second = _schedule_fact(db_session, location), _schedule_fact(db_session, location)

    transport.deliver(first)
    transport.deliver(second)
    assert len(fake.patches()) == 1

    location.phone = "+41441111111"
    db_session.commit()
    transport.deliver(_schedule_fact(db_session, location))
    assert len(fake.patches()) == 2
    assert b"+41441111111" in fake.patches()[-1].content


def test_a_department_google_offers_no_type_for_is_left_out_and_recorded(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    db_session.add(
        LocationOpeningHours(
            location_id=location.id, department=LocationDepartment.SERVICE, weekday=6, opens_at=T(8), closes_at=T(12)
        )
    )
    db_session.add(
        LocationOpeningHours(
            location_id=location.id, department=LocationDepartment.PARTS, weekday=6, opens_at=T(9), closes_at=T(11)
        )
    )
    db_session.commit()
    fake.more_hours_types = ["SERVICE", "DELIVERY"]

    _transport(engine).deliver(_schedule_fact(db_session, location))

    [patch] = fake.patches()
    assert "moreHours" in patch.url.params["updateMask"]
    assert b'"hoursTypeId": "SERVICE"' in patch.content or b'"hoursTypeId":"SERVICE"' in patch.content
    assert _state(db_session, binding).unmapped_departments == ["parts"]
    assert len([r for r in fake.requests if r.url.params.get("readMask") == "categories"]) == 1


def test_a_transient_failure_raises_for_the_retry_and_dead_letters_after_the_last_attempt(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    message = _schedule_fact(db_session, location)
    fake.patch_failure_status = 503

    with pytest.raises(DeliveryError):
        _transport(engine).deliver(message)
    assert _state(db_session, binding) is None or _state(db_session, binding).source_hash is None

    # Through the worker: the last attempt dead-letters the message (its alert).
    message.attempts = MAX_ATTEMPTS - 1
    message.next_attempt_at = utcnow() - dt.timedelta(seconds=1)
    db_session.commit()
    result = poll_once(db_session, _transport(engine))
    db_session.expire_all()
    assert result.dead == 1 and db_session.get(OutboxMessage, message.id).status == OutboxStatus.DEAD

    # Once Google answers again, a redelivery pushes.
    fake.patch_failure_status = None
    _transport(engine).deliver(message)
    assert _state(db_session, binding).source_hash is not None


def test_google_refusing_the_request_is_not_swallowed(db_session, engine, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    _bind(db_session, google, dealership.id, location.id)
    fake.patch_failure_status = 400
    with pytest.raises(DeliveryError):
        _transport(engine).deliver(_schedule_fact(db_session, location))


def test_a_refused_grant_moves_the_binding_to_error_and_is_not_retried(
    db_session, engine, google, client_connection, betrieb
):
    fake, secrets_store = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    fake.refused_refresh_tokens.add(secrets_store.values[(binding.id, "refresh_token")])

    _transport(engine).deliver(_schedule_fact(db_session, location))  # no DeliveryError: consumed

    db_session.expire_all()
    refused = db_session.get(GbpLocationBinding, binding.id)
    assert refused.status == GbpBindingStatus.ERROR and refused.error_code == "grant_refused"
    assert fake.patches() == []


@pytest.mark.parametrize("status", [GbpBindingStatus.PENDING, GbpBindingStatus.ERROR, GbpBindingStatus.DISCONNECTED])
def test_a_location_without_a_live_binding_sends_nothing(
    db_session, engine, google, client_connection, betrieb, status
):
    fake, _ = google
    dealership, location = betrieb
    _bind(db_session, google, dealership.id, location.id, status=status)
    _transport(engine).deliver(_schedule_fact(db_session, location))
    assert fake.requests == []


def test_no_binding_at_all_and_an_inactive_site_send_nothing(db_session, engine, google, client_connection, betrieb):
    fake, _ = google
    dealership, location = betrieb
    _transport(engine).deliver(_schedule_fact(db_session, location))
    _bind(db_session, google, dealership.id, location.id)
    location.is_active = False
    db_session.commit()
    _transport(engine).deliver(_schedule_fact(db_session, location))
    assert fake.requests == []


def test_a_fact_naming_another_betrieb_never_reaches_this_betriebs_binding(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    _bind(db_session, google, dealership.id, location.id)
    other = _make_dealership(db_session)
    message = _schedule_fact(db_session, location)
    message.tenant_id = other.id
    message.payload = {**message.payload, "dealershipId": str(other.id)}
    db_session.commit()

    _transport(engine).deliver(message)

    assert fake.requests == []


# -- removals reach Google -----------------------------------------------------------------------


def test_a_field_pushed_before_and_empty_now_is_masked_and_cleared():
    """A removal in Nexotec reaches Google: the last closure deleted, a
    department that lost its own hours, the phone cleared."""

    before = gbp_push.build_patch(
        _facts(
            opening_hours=(_hours("sales", 1, second=None), _hours("service", 2, second=None)),
            closed_days=(TODAY,),
        ),
        offered_hours_types=frozenset({"SERVICE"}),
    )
    assert {"specialHours", "moreHours", "phoneNumbers.primaryPhone"} <= set(before.pushed_fields)

    after = gbp_push.build_patch(
        _facts(phone=None, opening_hours=(_hours("sales", 1, second=None),), closed_days=()),
        offered_hours_types=frozenset({"SERVICE"}),
        previously_pushed=frozenset(before.pushed_fields),
    )

    assert {"specialHours", "moreHours", "phoneNumbers.primaryPhone"} <= set(after.update_mask)
    assert after.fields["specialHours"] == {}
    assert after.fields["moreHours"] == []
    assert after.fields["phoneNumbers"] == {}
    # What is cleared is not «pushed» any more: the next push does not clear it again.
    assert not {"specialHours", "moreHours", "phoneNumbers.primaryPhone"} & set(after.pushed_fields)


def test_a_department_that_now_has_the_main_hours_is_cleared_from_more_hours():
    before = gbp_push.build_patch(
        _facts(opening_hours=(_hours("sales", 1, second=None), _hours("service", 2, second=None))),
        offered_hours_types=frozenset({"SERVICE"}),
    )
    after = gbp_push.build_patch(
        _facts(opening_hours=(_hours("sales", 1, second=None), _hours("service", 1, second=None))),
        offered_hours_types=frozenset({"SERVICE"}),
        previously_pushed=frozenset(before.pushed_fields),
    )
    assert "moreHours" in after.update_mask and after.fields["moreHours"] == []


def test_a_field_never_pushed_stays_out_of_the_mask_whatever_was_pushed_before():
    after = gbp_push.build_patch(
        _facts(phone=None, opening_hours=(_hours("sales", 1, second=None),), closed_days=()),
        offered_hours_types=frozenset(),
        previously_pushed=frozenset({"storefrontAddress", "regularHours"}),
    )
    assert not {"specialHours", "moreHours", "phoneNumbers.primaryPhone"} & set(after.update_mask)
    assert "phoneNumbers" not in after.fields and "specialHours" not in after.fields


def test_the_website_removed_is_masked_without_a_value_and_the_address_is_never_cleared():
    after = gbp_push.build_patch(
        _facts(website=None, address_postal_code=None, opening_hours=()),
        offered_hours_types=frozenset(),
        previously_pushed=frozenset({"websiteUri", "storefrontAddress", "regularHours"}),
    )
    assert "websiteUri" in after.update_mask and "websiteUri" not in after.fields
    assert after.fields["regularHours"] == {}
    assert "storefrontAddress" not in after.update_mask


def test_deleting_the_last_closure_and_clearing_the_phone_reach_google(
    db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    binding = _bind(db_session, google, dealership.id, location.id)
    closure = LocationClosure(
        location_id=location.id, starts_on=today_in_session_zone()[0], ends_on=today_in_session_zone()[0], reason="x"
    )
    db_session.add(closure)
    db_session.commit()
    _transport(engine).deliver(_schedule_fact(db_session, location))
    assert "specialHours" in _state(db_session, binding).pushed_fields

    db_session.delete(closure)
    location.phone = None
    db_session.commit()
    _transport(engine).deliver(_schedule_fact(db_session, location))

    second = fake.patches()[-1]
    mask = second.url.params["updateMask"].split(",")
    assert "specialHours" in mask and "phoneNumbers.primaryPhone" in mask
    body = json.loads(second.content)
    assert body["specialHours"] == {} and body["phoneNumbers"] == {}
    assert not {"specialHours", "phoneNumbers.primaryPhone"} & set(_state(db_session, binding).pushed_fields)


# -- the activation ---------------------------------------------------------------------------------


def test_picking_the_google_location_states_the_activation_which_pushes(
    client, db_session, engine, google, client_connection
):
    fake, _ = google
    dealership = _make_dealership(db_session)
    manager = make_user(db_session, tenant_id=dealership.id, email=f"chef-{uuid.uuid4().hex[:6]}@example.test")
    location = _site(db_session, dealership.id)
    binding = _link(client, fake, dealership, manager, location)["binding"]
    assert _pick(client, dealership, manager, binding, location=LOCATION_2).status_code == 200

    message = _latest(db_session, gbp_link.BINDING_ACTIVATED_EVENT)
    assert message.tenant_id == dealership.id and message.payload["locationId"] == str(location.id)
    assert message.payload["bindingId"] == binding["id"]

    _transport(
        engine,
        consumer_name="integration.business_profile_push_on_binding_activated",
        event_type=gbp_link.BINDING_ACTIVATED_EVENT,
    ).deliver(message)

    [patch] = fake.patches()
    assert str(patch.url).split("?")[0].endswith(LOCATION_2)
    assert fake.calls("/media") == []  # photos wait for KAN-784
