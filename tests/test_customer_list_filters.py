"""List filters (FR-02): `?customerType=`, `?language=`, `?canton=` on the
customer list endpoint, on top of the existing `lifecycleStatus`/`updatedSince`
filters. Combined-filter and zero-result cases matter most here — each filter
clause is a trivial `.where()`, so the risk is in how they compose, not in any
one clause individually.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.auth import AccessRole, create_access_token
from tests.dealership_support import valid_uid
from tests.postgres_only import TIMESTAMPTZ
from tests.token_support import dealer_group_of

ZH_ADDRESS = {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"}
VD_ADDRESS = {"street": "Rue de Bourg", "houseNumber": "1", "postalCode": "1000", "locality": "Lausanne", "canton": "VD"}


def _as_customer_addresses(flat: dict, address_type: str = "domicile") -> list[dict]:
    """Customer's own nested `addresses` shape (WP-3 PR-5, ADR-067) — field
    names match CustomerAddress's own attributes, unlike Dealership's flat
    ZH_ADDRESS/VD_ADDRESS shape above. Canton is never sent; it's derived
    server-side from the postal code.
    """

    return [
        {
            "addressType": address_type,
            "addressStreet": flat["street"],
            "addressHouseNumber": flat["houseNumber"],
            "addressPostalCode": flat["postalCode"],
            "addressLocality": flat["locality"],
        }
    ]


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None, *, is_dealer_manager: bool = False) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=_tid,
        group_id=dealer_group_of(str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent", "address": ZH_ADDRESS, "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna", "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _list(client, dealer_id: str, **query):
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params=query, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


def _business_email() -> dict:
    return {"emailType": "work", "emailAddress": f"biz-{uuid.uuid4().hex[:8]}@example.ch"}


# --- customerType --------------------------------------------------------------------


def test_filter_by_customer_type_individual(client):
    dealer_id = _create_dealer(client)
    individual = _create_customer(client, dealer_id, lastName="Solo")
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual")
    ids = [c["id"] for c in body["items"]]
    assert ids == [individual["id"]]


def test_filter_by_customer_type_business(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Solo")
    business = _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None,
        companyName="Alpha AG", emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="business")
    ids = [c["id"] for c in body["items"]]
    assert ids == [business["id"]]


# --- language --------------------------------------------------------------------


def test_filter_by_language(client):
    dealer_id = _create_dealer(client)
    de_customer = _create_customer(client, dealer_id, lastName="Deutsch", language="de")
    _create_customer(client, dealer_id, lastName="Français", language="fr")

    body = _list(client, dealer_id, language="de")
    ids = [c["id"] for c in body["items"]]
    assert ids == [de_customer["id"]]


# --- canton (derived from address postal code) ------------------------------------------


def test_filter_by_canton(client):
    dealer_id = _create_dealer(client)
    zh_customer = _create_customer(client, dealer_id, lastName="Zurich", addresses=_as_customer_addresses(ZH_ADDRESS))
    _create_customer(client, dealer_id, lastName="Vaud", addresses=_as_customer_addresses(VD_ADDRESS))

    body = _list(client, dealer_id, canton="ZH")
    ids = [c["id"] for c in body["items"]]
    assert ids == [zh_customer["id"]]


# --- combined filters -----------------------------------------------------------------


def test_combined_filters_narrow_correctly(client):
    dealer_id = _create_dealer(client)
    target = _create_customer(client, dealer_id, lastName="Match", language="fr", addresses=_as_customer_addresses(VD_ADDRESS))
    _create_customer(client, dealer_id, lastName="WrongLanguage", language="de", addresses=_as_customer_addresses(VD_ADDRESS))
    _create_customer(client, dealer_id, lastName="WrongCanton", language="fr", addresses=_as_customer_addresses(ZH_ADDRESS))
    _create_customer(
        client, dealer_id, customerType="business", firstName=None, lastName=None, companyName="Beta AG",
        language="fr", addresses=_as_customer_addresses(VD_ADDRESS), emails=[_business_email()],
    )

    body = _list(client, dealer_id, customer_type="individual", language="fr", canton="VD")
    ids = [c["id"] for c in body["items"]]
    assert ids == [target["id"]]


def test_combined_filters_with_no_matches_returns_empty(client):
    dealer_id = _create_dealer(client)
    _create_customer(client, dealer_id, lastName="Deutsch", language="de", addresses=_as_customer_addresses(ZH_ADDRESS))

    body = _list(client, dealer_id, customer_type="business", language="it", canton="GE")
    assert body["items"] == []
    assert body["total"] == 0


# --- updated more than N days ago (KAN-458) -----------------------------------------------


def _backdate(db_session, customer_id: str, when: dt.datetime) -> None:
    db_session.execute(text("UPDATE customer SET updated_at = :when WHERE id = :id"), {"when": when, "id": customer_id})
    db_session.commit()


def test_filter_by_updated_more_than_days_ago(client, db_session):
    """The grid's «Changed: more than 90 days ago» — the server resolves it
    against its own clock, so it can no longer be offered and dropped."""
    dealer_id = _create_dealer(client)
    stale = _create_customer(client, dealer_id, lastName="Stale")
    recent = _create_customer(client, dealer_id, lastName="Recent")
    _create_customer(client, dealer_id, lastName="Fresh")
    now = dt.datetime.now(dt.UTC)
    _backdate(db_session, stale["id"], now - dt.timedelta(days=120))
    _backdate(db_session, recent["id"], now - dt.timedelta(days=30))

    body = _list(client, dealer_id, updated_more_than_days_ago=90)
    assert [c["id"] for c in body["items"]] == [stale["id"]]
    assert body["total"] == 1


def test_updated_more_than_days_ago_counts_whole_swiss_calendar_days():
    """N days ago means before the start of that calendar day in Zürich — at
    00:30 Swiss time (still the previous day in UTC) the cut-off is Swiss
    midnight N days back, not UTC midnight."""
    from app.customer.services.customer_search import _start_of_day_days_ago

    zurich = ZoneInfo("Europe/Zurich")
    now = dt.datetime(2026, 10, 26, 0, 30, tzinfo=zurich)
    assert _start_of_day_days_ago(0, now) == dt.datetime(2026, 10, 26, tzinfo=zurich)
    assert _start_of_day_days_ago(90, now) == dt.datetime(2026, 7, 28, tzinfo=zurich)


def test_updated_more_than_days_ago_refuses_a_negative_count(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get("/v1/customers", params={"updated_more_than_days_ago": -1}, headers=_bearer(token))
    assert response.status_code == 422, response.text


# --- relative «Geändert» conditions resolved server-side (KAN-474) --------------------------
#
# «heute», «diese Woche», «dieser Monat», «dieses Jahr» and «in den letzten N
# Tagen» were resolved in the browser and sent as `updated_since=YYYY-MM-DD`,
# which the API bound as UTC midnight: one to two hours late against the
# Zurich midnight «vor mehr als N Tagen» uses (KAN-458). U-09: relative dates
# are resolved by the server, in the session time zone.

ZURICH = ZoneInfo("Europe/Zurich")
# Wednesday 28 October 2026, 00:30 in Zurich — still Tuesday the 27th in UTC.
BOUNDARY_NOW = dt.datetime(2026, 10, 28, 0, 30, tzinfo=ZURICH)


def test_relative_periods_start_at_swiss_midnight_at_00_30_zurich():
    from app.customer.schemas.customer import CalendarPeriod
    from app.customer.services.customer_search import _start_of_current_period

    assert BOUNDARY_NOW.astimezone(dt.UTC).date() == dt.date(2026, 10, 27)  # the UTC day is still yesterday
    assert _start_of_current_period(CalendarPeriod.DAY, BOUNDARY_NOW) == dt.datetime(2026, 10, 28, tzinfo=ZURICH)
    # ISO week: Monday 26 October
    assert _start_of_current_period(CalendarPeriod.WEEK, BOUNDARY_NOW) == dt.datetime(2026, 10, 26, tzinfo=ZURICH)
    # 1 October, still summer time (+02:00) — the zone's own offset on that day
    month = _start_of_current_period(CalendarPeriod.MONTH, BOUNDARY_NOW)
    assert month == dt.datetime(2026, 10, 1, tzinfo=ZURICH)
    assert month.utcoffset() == dt.timedelta(hours=2)
    assert _start_of_current_period(CalendarPeriod.YEAR, BOUNDARY_NOW) == dt.datetime(2026, 1, 1, tzinfo=ZURICH)


def test_in_the_last_n_days_starts_at_swiss_midnight_at_00_30_zurich():
    from app.customer.services.customer_search import _start_of_day_days_ago

    assert _start_of_day_days_ago(30, BOUNDARY_NOW) == dt.datetime(2026, 9, 28, tzinfo=ZURICH)


def _period_start(period: str) -> dt.datetime:
    from app.customer.schemas.customer import CalendarPeriod
    from app.customer.services.customer_search import _start_of_current_period

    return _start_of_current_period(CalendarPeriod(period))



@TIMESTAMPTZ
@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_filter_by_updated_since_start_of(client, db_session, period):
    """A customer changed at the period's Zurich midnight is in; one changed
    a second before it is out — the UTC-midnight bound kept the latter for
    up to two hours."""
    dealer_id = _create_dealer(client)
    inside = _create_customer(client, dealer_id, lastName="Inside")
    outside = _create_customer(client, dealer_id, lastName="Outside")
    start = _period_start(period)
    _backdate(db_session, inside["id"], start)
    _backdate(db_session, outside["id"], start - dt.timedelta(seconds=1))

    body = _list(client, dealer_id, updated_since_start_of=period)
    assert [c["id"] for c in body["items"]] == [inside["id"]]


@TIMESTAMPTZ
def test_in_the_last_and_more_than_n_days_ago_partition_the_list(client, db_session):
    """«in den letzten 30 Tagen» and «vor mehr als 30 Tagen» leave no gap and
    never overlap, also for a change in the hour after Zurich midnight."""
    from app.customer.services.customer_search import _start_of_day_days_ago

    dealer_id = _create_dealer(client)
    boundary = _start_of_day_days_ago(30)
    stamps = {
        "at-midnight": boundary,
        "half-past-midnight": boundary + dt.timedelta(minutes=30),
        "second-before": boundary - dt.timedelta(seconds=1),
        "long-ago": boundary - dt.timedelta(days=200),
        "yesterday": boundary + dt.timedelta(days=29),
    }
    ids = {}
    for name, when in stamps.items():
        ids[name] = _create_customer(client, dealer_id, lastName=name)["id"]
        _backdate(db_session, ids[name], when)

    recent = {c["id"] for c in _list(client, dealer_id, updated_in_last_days=30, limit=50)["items"]}
    older = {c["id"] for c in _list(client, dealer_id, updated_more_than_days_ago=30, limit=50)["items"]}
    assert recent == {ids["at-midnight"], ids["half-past-midnight"], ids["yesterday"]}
    assert older == {ids["second-before"], ids["long-ago"]}
    assert recent.isdisjoint(older)
    assert recent | older == set(ids.values())


def test_relative_parameters_refuse_unknown_values(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    for params in ({"updated_in_last_days": -1}, {"updated_since_start_of": "fortnight"}):
        response = client.get("/v1/customers", params=params, headers=_bearer(token))
        assert response.status_code == 422, (params, response.text)
