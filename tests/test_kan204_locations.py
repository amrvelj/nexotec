"""KAN-204 — Locations: full master data, opening hours per department with
the one-set default, cantonal holiday reference data, closures (Dealer
Administration PRD › Locations; D-A-02, D-A-27, FR-A-02, FR-A-03, A-RULE-1).

What is pinned here:
- the holiday reference data: 26 cantons, every canton's set equal to the
  SECO table's column (transcribed once more here, from the table, as the
  check on the seed's per-calendar transcription), the eight-day ceiling of
  Art. 20a ArG, and the computed dates of the rules (Easter, the Näfelser
  Fahrt's Holy Week shift, the Jeûne genevois, the Lundi du Jeûne, the
  Monday-only and the Tuesday/Saturday conditions, Solothurn's afternoon);
- the API: CRUD with If-Match, Idempotency-Key honoured on create, no
  delete, the code fixed once saved and unique per Betrieb, FR-A-03 (the
  first location made with the Betrieb, the last active one never
  deactivated), the matrix row (any role reads, the manager writes), 404
  across tenants, the bank-account override existing only for banking roles;
- opening hours stored per department always (the one-set mode writes three
  identical rows a day), closures, and user_location refusing a deactivated
  site for a new assignment.
"""

import datetime as dt
import importlib.util
import uuid
from pathlib import Path

import pytest

from app.core.auth import AccessRole
from app.core.constants import CANTON_CODES
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.holiday_calendar import Holiday, HolidayCalendar, HolidayCondition, HolidayRule
from app.platform.organisation.models.location_schedule import LocationOpeningHours
from app.platform.organisation.services import holidays as holiday_service
from app.platform.reference_data_seed.holidays import CALENDARS, HOLIDAYS, SOURCE
from tests.test_dealership import _bearer, _create_dealership, _token

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "platform"
PLAIN_IBAN = "CH93 0076 2011 6238 5295 7"

# --- the reference data ----------------------------------------------------------------

_ALL = frozenset(CANTON_CODES)  # the 26 cantons

# The SECO table (Stand am 12.12.2024), read column by column: which cantons
# carry each holiday anywhere in their territory.
SECO_TABLE = {
    "new_year": _ALL,
    "berchtold": {"AG", "BE", "FR", "NE", "TG", "VD"},
    "epiphany": {"TI"},
    "republic_neuchatel": {"NE"},
    "saint_joseph": {"SZ", "VS"},
    "naefelser_fahrt": {"GL"},
    "good_friday": _ALL - {"TI", "VS"},
    "easter_monday": {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "SG", "SH", "TG", "TI", "VD",
                      "ZH"},
    "labour_day": {"BL", "BS", "JU", "NE", "SH", "SO", "ZH"},
    "ascension": _ALL,
    "whit_monday": {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GR", "JU", "SG", "SH", "TG", "VD", "ZH"},
    "corpus_christi": {"AG", "AI", "FR", "JU", "LU", "NE", "NW", "OW", "SO", "SZ", "UR", "VS", "ZG"},
    "national_day": _ALL,
    "assumption": {"AG", "FR", "LU", "NW", "OW", "SO", "SZ", "TI", "UR", "VS", "ZG"},
    "jeune_genevois": {"GE"},
    "federal_fast_monday": {"VD"},
    "all_saints": {"AG", "FR", "GL", "LU", "NW", "OW", "SG", "SO", "SZ", "TI", "UR", "VS", "ZG"},
    "immaculate_conception": {"AG", "FR", "NW", "OW", "UR", "VS", "ZG"},
    "christmas": _ALL,
    "st_stephen": {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GL", "GR", "LU", "NE", "SG", "SH", "TG", "TI", "ZH"},
    "restoration_geneva": {"GE"},
}


def test_every_canton_has_a_calendar_and_each_holiday_matches_the_seco_table():
    assert {c.canton for c in CALENDARS} == _ALL
    assert len({c.code for c in CALENDARS}) == len(CALENDARS)
    seen: dict[str, set[str]] = {}
    for calendar in CALENDARS:
        for entry in calendar.holidays:
            seen.setdefault(entry.code, set()).add(calendar.canton)
    assert seen == {code: set(cantons) for code, cantons in SECO_TABLE.items()}


def test_no_calendar_holds_more_than_eight_cantonal_days_besides_the_first_of_august():
    # Art. 20a para. 1 ArG: at most eight cantonal holidays equal to Sundays.
    # Le Landeron is the one published exception to the count, by its Monday-only days.
    for calendar in CALENDARS:
        counted = [e for e in calendar.holidays if e.code != "national_day" and e.condition != "only_on_monday"]
        assert len(counted) <= 8, calendar.code
        assert "national_day" in {e.code for e in calendar.holidays}, calendar.code


def test_a_canton_without_regional_variants_has_its_own_code_and_the_variant_cantons_have_none():
    by_canton: dict[str, list[str]] = {}
    for calendar in CALENDARS:
        by_canton.setdefault(calendar.canton, []).append(calendar.code)
    for canton, codes in by_canton.items():
        if len(codes) == 1:
            assert codes == [canton]
    assert "AG" not in by_canton["AG"] and len(by_canton["AG"]) == 6
    assert sorted(by_canton["FR"]) == ["FR-CATHOLIC", "FR-REFORMED"]
    assert sorted(by_canton["NE"]) == ["NE", "NE-LANDERON"]
    assert sorted(by_canton["SO"]) == ["SO", "SO-BUCHEGGBERG"]


def test_the_migration_seeds_from_the_same_rows_and_names_its_source():
    path = next(_MIGRATION.glob("83d8634ff337_*.py"))
    spec = importlib.util.spec_from_file_location("kan204_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.CALENDARS is CALENDARS and migration.HOLIDAYS is HOLIDAYS
    assert "seco.admin.ch" in path.read_text(encoding="utf-8")
    assert "Stand am 12.12.2024" in SOURCE


def seed_holiday_calendars(db_session) -> dict[str, HolidayCalendar]:
    """The calendars as the migration seeds them — per test, not autouse."""

    out = {}
    for order, calendar in enumerate(CALENDARS):
        row = HolidayCalendar(
            code=calendar.code,
            canton=calendar.canton,
            label_de=calendar.label_de,
            label_fr=calendar.label_fr,
            label_it=calendar.label_it,
            label_en=calendar.label_en,
            source=SOURCE,
            sort_order=order,
            active=True,
        )
        db_session.add(row)
        db_session.flush()
        for holiday_order, entry in enumerate(calendar.holidays):
            definition = HOLIDAYS[entry.code]
            db_session.add(
                Holiday(
                    calendar_id=row.id,
                    code=entry.code,
                    label_de=definition.label_de,
                    label_fr=definition.label_fr,
                    label_it=definition.label_it,
                    label_en=definition.label_en,
                    rule=HolidayRule(definition.rule),
                    month=definition.month,
                    day=definition.day,
                    easter_offset_days=definition.easter_offset_days,
                    condition=HolidayCondition(entry.condition),
                    afternoon_only=entry.afternoon_only,
                    sort_order=holiday_order,
                )
            )
        out[calendar.code] = row
    db_session.commit()
    return out


@pytest.mark.parametrize(
    ("year", "easter"),
    [(2024, dt.date(2024, 3, 31)), (2025, dt.date(2025, 4, 20)), (2026, dt.date(2026, 4, 5)),
     (2027, dt.date(2027, 3, 28)), (2038, dt.date(2038, 4, 25)), (1985, dt.date(1985, 4, 7))],
)
def test_easter_sunday_is_the_gregorian_computus(year, easter):
    assert holiday_service.easter_sunday(year) == easter


def _dates(db_session, calendars, code: str, year: int) -> dict[str, dt.date]:
    rows = holiday_service.holidays_by_calendar(db_session, [calendars[code].id])[calendars[code].id]
    return {d.holiday.code: d.date for d in holiday_service.dates_in_year(rows, year)}


def test_the_named_rules_compute_their_published_dates(db_session):
    calendars = seed_holiday_calendars(db_session)
    # Glarus 2026: the first Thursday of April (2 April) is Maundy Thursday,
    # so the Fahrt moves a week — the Glarus government's 9 April.
    assert _dates(db_session, calendars, "GL", 2026)["naefelser_fahrt"] == dt.date(2026, 4, 9)
    assert _dates(db_session, calendars, "GL", 2025)["naefelser_fahrt"] == dt.date(2025, 4, 3)
    assert _dates(db_session, calendars, "GE", 2025)["jeune_genevois"] == dt.date(2025, 9, 11)
    assert _dates(db_session, calendars, "GE", 2026)["jeune_genevois"] == dt.date(2026, 9, 10)
    assert _dates(db_session, calendars, "VD", 2025)["federal_fast_monday"] == dt.date(2025, 9, 22)
    zh = _dates(db_session, calendars, "ZH", 2026)
    assert zh["good_friday"] == dt.date(2026, 4, 3)
    assert zh["ascension"] == dt.date(2026, 5, 14)
    assert zh["whit_monday"] == dt.date(2026, 5, 25)
    assert _dates(db_session, calendars, "LU", 2026)["corpus_christi"] == dt.date(2026, 6, 4)


def test_the_published_conditions_drop_the_days_the_canton_does_not_observe(db_session):
    calendars = seed_holiday_calendars(db_session)
    # Neuchâtel: 2 January and 26 December only on a Monday.
    assert "berchtold" in _dates(db_session, calendars, "NE", 2023)  # Monday
    assert "berchtold" not in _dates(db_session, calendars, "NE", 2025)  # Thursday
    assert "st_stephen" in _dates(db_session, calendars, "NE", 2022)  # Monday
    assert "st_stephen" not in _dates(db_session, calendars, "NE", 2025)  # Friday
    # Appenzell: no St Stephen's Day on a Tuesday or a Saturday.
    for canton in ("AI", "AR"):
        assert "st_stephen" not in _dates(db_session, calendars, canton, 2023)  # Tuesday
        assert "st_stephen" in _dates(db_session, calendars, canton, 2025)  # Friday
        assert "st_stephen" not in _dates(db_session, calendars, canton, 2026)  # Saturday
    # Elsewhere 26 December always counts.
    assert "st_stephen" in _dates(db_session, calendars, "ZH", 2026)


# --- API helpers ---------------------------------------------------------------------------


def _manager(dealership_id: str) -> dict[str, str]:
    return _bearer(_token(tenant_id=uuid.UUID(dealership_id), is_dealer_manager=True))


def _role(dealership_id: str, role: AccessRole) -> dict[str, str]:
    return _bearer(_token(role, tenant_id=uuid.UUID(dealership_id)))


def _payload(**overrides):
    payload = {
        "code": "zh-nord",
        "name": "Zürich Nord",
        "locationTypes": ["showroom", "workshop"],
        "addressStreet": "Thurgauerstrasse",
        "addressHouseNumber": "40",
        "addressPostalCode": "8050",
        "addressLocality": "Zürich",
        "addressCanton": "ZH",
        "contactPerson": "Anna Muster",
        "phone": "+41441234567",
        "email": "nord@garage.ch",
        "leadEmail": "leads-nord@garage.ch",
        "geoLat": 47.4141,
        "geoLng": 8.5469,
    }
    payload.update(overrides)
    return payload


def _create(client, dealership_id: str, **overrides) -> dict:
    response = client.post(
        f"/v1/dealerships/{dealership_id}/locations", json=_payload(**overrides), headers=_manager(dealership_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client, dealership_id: str, location: dict, body: dict, headers=None):
    return client.patch(
        f"/v1/dealerships/{dealership_id}/locations/{location['id']}",
        json=body,
        headers={**(headers or _manager(dealership_id)), "If-Match": str(location["version"])},
    )


def _list(client, dealership_id: str, headers=None, query: str = "") -> list[dict]:
    response = client.get(f"/v1/dealerships/{dealership_id}/locations{query}", headers=headers or _manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- FR-A-03: the first location -------------------------------------------------------------


def test_a_new_betrieb_starts_with_one_active_location_from_its_seat(client, db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _create_dealership(client)
    [first] = _list(client, dealership["id"])
    assert first["name"] == "Garage Musterbetrieb AG"
    assert first["isActive"] is True
    assert first["code"] is None
    assert (first["addressStreet"], first["addressPostalCode"], first["addressCanton"]) == ("Bahnhofstrasse", "8001", "ZH")
    assert first["phone"] == dealership["phone"]
    assert first["holidayCalendarId"] == str(calendars["ZH"].id)
    assert first["dealershipId"] == dealership["id"]


def test_the_last_active_location_cannot_be_deactivated_and_another_can(client):
    dealership = _create_dealership(client)
    [first] = _list(client, dealership["id"])
    refused = _patch(client, dealership["id"], first, {"isActive": False})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "last_active_location"

    second = _create(client, dealership["id"])
    deactivated = _patch(client, dealership["id"], first, {"isActive": False})
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["isActive"] is False
    # The deactivated site is still there (A-RULE-1), and the other is now the last.
    assert {row["id"] for row in _list(client, dealership["id"])} == {first["id"], second["id"]}
    assert [row["id"] for row in _list(client, dealership["id"], query="?active=true")] == [second["id"]]
    again = _patch(client, dealership["id"], second, {"isActive": False})
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "last_active_location"


def test_there_is_no_delete(client):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    response = client.delete(
        f"/v1/dealerships/{dealership['id']}/locations/{location['id']}",
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 405


# --- master data --------------------------------------------------------------------------------


def test_create_reads_back_every_field_and_audits(client, db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    assert location["code"] == "ZH-NORD"
    assert location["locationTypes"] == ["showroom", "workshop"]
    assert location["phone"] == "+41441234567"
    assert (location["geoLat"], location["geoLng"]) == (47.4141, 8.5469)
    assert location["holidayCalendarId"] == str(calendars["ZH"].id)  # the canton's own
    assert location["leadEmail"] == "leads-nord@garage.ch"
    assert location["calendarRef"] is None and location["warehouseRef"] is None
    fetched = client.get(
        f"/v1/dealerships/{dealership['id']}/locations/{location['id']}", headers=_manager(dealership["id"])
    ).json()
    assert fetched == location
    log = client.get(f"/v1/dealerships/{dealership['id']}/audit-log", headers=_manager(dealership["id"])).json()
    created = [e for e in log["items"] if e["entityType"] == "location" and e["entityId"] == location["id"]]
    assert created and created[0]["after"]["code"] == "ZH-NORD"


def test_a_canton_with_regional_calendars_leaves_the_pick_to_the_dealer(client, db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _create_dealership(client)
    baden = _create(
        client, dealership["id"], code="AG-BADEN", addressPostalCode="5400", addressLocality="Baden", addressCanton="AG"
    )
    assert baden["holidayCalendarId"] is None
    picked = _patch(client, dealership["id"], baden, {"holidayCalendarId": str(calendars["AG-BADEN"].id)})
    assert picked.status_code == 200 and picked.json()["holidayCalendarId"] == str(calendars["AG-BADEN"].id)
    unknown = _patch(client, dealership["id"], picked.json(), {"holidayCalendarId": str(uuid.uuid4())})
    assert unknown.status_code == 422 and unknown.json()["error"]["details"]["reason"] == "unknown_holiday_calendar"


def test_the_code_is_unique_per_betrieb_and_fixed_once_saved(client):
    dealership = _create_dealership(client)
    other = _create_dealership(client)
    location = _create(client, dealership["id"])
    duplicate = client.post(
        f"/v1/dealerships/{dealership['id']}/locations", json=_payload(code="ZH-NORD"), headers=_manager(dealership["id"])
    )
    assert duplicate.status_code == 409 and duplicate.json()["error"]["details"]["reason"] == "location_code_taken"
    _create(client, other["id"])  # another Betrieb may use the same code

    changed = _patch(client, dealership["id"], location, {"code": "ZH-SUED"})
    assert changed.status_code == 422 and changed.json()["error"]["details"]["reason"] == "location_code_immutable"
    same = _patch(client, dealership["id"], location, {"code": "zh-nord", "name": "Zürich-Nord"})
    assert same.status_code == 200 and same.json()["code"] == "ZH-NORD"

    # The first location was saved without one: it gets its code once.
    [first] = [row for row in _list(client, dealership["id"]) if row["code"] is None]
    set_once = _patch(client, dealership["id"], first, {"code": "HQ"})
    assert set_once.status_code == 200 and set_once.json()["code"] == "HQ"
    again = _patch(client, dealership["id"], set_once.json(), {"code": "HQ2"})
    assert again.status_code == 422
    malformed = client.post(
        f"/v1/dealerships/{dealership['id']}/locations", json=_payload(code="x y"), headers=_manager(dealership["id"])
    )
    assert malformed.status_code == 422


def test_the_address_geo_and_validity_are_judged_as_a_whole(client):
    dealership = _create_dealership(client)
    missing = client.post(
        f"/v1/dealerships/{dealership['id']}/locations",
        json=_payload(addressHouseNumber=None),
        headers=_manager(dealership["id"]),
    )
    assert missing.status_code == 422 and missing.json()["error"]["details"]["reason"] == "location_address_incomplete"
    no_canton = client.post(
        f"/v1/dealerships/{dealership['id']}/locations",
        json=_payload(addressCanton=None),
        headers=_manager(dealership["id"]),
    )
    assert no_canton.status_code == 422 and no_canton.json()["error"]["details"]["reason"] == "location_canton_required"
    location = _create(client, dealership["id"])
    half_geo = _patch(client, dealership["id"], location, {"geoLng": None})
    assert half_geo.status_code == 422 and half_geo.json()["error"]["details"]["reason"] == "location_geo_incomplete"
    inverted = _patch(client, dealership["id"], location, {"validFrom": "2027-01-01", "validTo": "2026-01-01"})
    assert inverted.status_code == 422 and inverted.json()["error"]["details"]["reason"] == "location_validity_inverted"
    moved = _patch(client, dealership["id"], location, {"addressPostalCode": "8001", "addressCountry": "ch"})
    assert moved.status_code == 200 and moved.json()["addressCountry"] == "CH"


def test_a_row_from_before_kan204_saves_without_a_whole_address(client, db_session):
    dealership = _create_dealership(client)
    row = Location(tenant_id=uuid.UUID(dealership["id"]), name="Alt", address_locality="Baden", location_types=[])
    db_session.add(row)
    db_session.commit()
    [old] = [r for r in _list(client, dealership["id"]) if r["name"] == "Alt"]
    renamed = _patch(client, dealership["id"], old, {"name": "Baden alt", "locationTypes": ["storage"]})
    assert renamed.status_code == 200, renamed.text


def test_patch_needs_if_match_and_refuses_a_stale_one(client):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    url = f"/v1/dealerships/{dealership['id']}/locations/{location['id']}"
    assert client.patch(url, json={"name": "X"}, headers=_manager(dealership["id"])).status_code == 400
    assert _patch(client, dealership["id"], location, {"name": "X"}).status_code == 200
    assert _patch(client, dealership["id"], location, {"name": "Y"}).status_code == 409


def test_create_honours_the_idempotency_key(client):
    dealership = _create_dealership(client)
    headers = {**_manager(dealership["id"]), "Idempotency-Key": str(uuid.uuid4())}
    url = f"/v1/dealerships/{dealership['id']}/locations"
    first = client.post(url, json=_payload(), headers=headers)
    second = client.post(url, json=_payload(), headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_any_role_reads_only_the_manager_writes_and_another_betrieb_is_404(client):
    dealership = _create_dealership(client)
    other = _create_dealership(client)
    location = _create(client, dealership["id"])
    sales = _role(dealership["id"], AccessRole.SALES)
    assert _list(client, dealership["id"], headers=sales)
    write = client.post(f"/v1/dealerships/{dealership['id']}/locations", json=_payload(code="X"), headers=sales)
    assert write.status_code == 403
    assert _patch(client, dealership["id"], location, {"name": "X"}, headers=sales).status_code == 403
    foreign = _manager(other["id"])
    assert client.get(f"/v1/dealerships/{dealership['id']}/locations", headers=foreign).status_code == 404
    # Another Betrieb's location under one's own path is a 404 as well.
    assert (
        client.get(f"/v1/dealerships/{other['id']}/locations/{location['id']}", headers=foreign).status_code == 404
    )


def test_the_bank_account_override_exists_only_for_the_banking_roles(client):
    dealership = _create_dealership(client)
    other = _create_dealership(client)
    account = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts",
        json={"accountHolder": "Garage", "bankName": "ZKB", "iban": PLAIN_IBAN},
        headers=_manager(dealership["id"]),
    ).json()
    foreign_account = client.post(
        f"/v1/dealerships/{other['id']}/bank-accounts",
        json={"accountHolder": "Andere", "bankName": "UBS", "iban": PLAIN_IBAN},
        headers=_manager(other["id"]),
    ).json()
    location = _create(client, dealership["id"])
    refused = _patch(client, dealership["id"], location, {"bankAccountOverrideId": foreign_account["id"]})
    assert refused.status_code == 422 and refused.json()["error"]["details"]["reason"] == "unknown_bank_account"
    saved = _patch(client, dealership["id"], location, {"bankAccountOverrideId": account["id"]})
    assert saved.status_code == 200 and saved.json()["bankAccountOverrideId"] == account["id"]

    def override_for(role: AccessRole) -> str | None:
        rows = _list(client, dealership["id"], headers=_role(dealership["id"], role))
        return next(r for r in rows if r["id"] == location["id"])["bankAccountOverrideId"]

    assert override_for(AccessRole.FINANCE) == account["id"]
    assert override_for(AccessRole.SALES) is None

    # The account cannot be deleted while a location names it.
    deleted = client.delete(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}",
        headers={**_manager(dealership["id"]), "If-Match": str(account["version"])},
    )
    assert deleted.status_code == 409 and deleted.json()["error"]["details"]["reason"] == "bank_account_used_by_location"


# --- opening hours ----------------------------------------------------------------------------------


def _hours_url(dealership_id: str, location_id: str) -> str:
    return f"/v1/dealerships/{dealership_id}/locations/{location_id}/opening-hours"


def _put_hours(client, dealership_id: str, location: dict, body: dict, version: int | None = None):
    return client.put(
        _hours_url(dealership_id, location["id"]),
        json=body,
        headers={**_manager(dealership_id), "If-Match": str(version or location["version"])},
    )


def test_one_set_of_hours_is_stored_for_every_department(client, db_session):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    week = [
        {"weekday": day, "opensAt": "07:30", "closesAt": "12:00", "secondOpensAt": "13:15", "secondClosesAt": "18:00"}
        for day in range(1, 6)
    ] + [{"weekday": 6, "opensAt": "09:00", "closesAt": "16:00"}]
    response = _put_hours(client, dealership["id"], location, {"perDepartment": False, "hours": week})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["perDepartment"] is False
    assert body["locationVersion"] == location["version"] + 1
    assert len(body["hours"]) == 18  # six days × sales, service, parts
    rows = db_session.query(LocationOpeningHours).filter_by(location_id=uuid.UUID(location["id"])).all()
    assert {r.department.value for r in rows} == {"sales", "service", "parts"}
    assert {(r.weekday, r.opens_at) for r in rows if r.department.value == "parts"} == {
        (d, dt.time(7, 30)) for d in range(1, 6)
    } | {(6, dt.time(9, 0))}
    # The stale version is refused; the read says the same as the PUT.
    stale = _put_hours(client, dealership["id"], location, {"perDepartment": False, "hours": []})
    assert stale.status_code == 409
    read = client.get(_hours_url(dealership["id"], location["id"]), headers=_role(dealership["id"], AccessRole.SALES))
    assert read.json() == body


def test_hours_per_department_are_read_back_as_differing(client):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    hours = [
        {"department": "sales", "weekday": 6, "opensAt": "09:00", "closesAt": "16:00"},
        {"department": "service", "weekday": 1, "opensAt": "07:00", "closesAt": "17:00"},
    ]
    response = _put_hours(client, dealership["id"], location, {"perDepartment": True, "hours": hours})
    assert response.status_code == 200, response.text
    assert response.json()["perDepartment"] is True
    assert len(response.json()["hours"]) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"perDepartment": False, "hours": [{"department": "sales", "weekday": 1, "opensAt": "08:00", "closesAt": "12:00"}]},
        {"perDepartment": True, "hours": [{"weekday": 1, "opensAt": "08:00", "closesAt": "12:00"}]},
        {"perDepartment": False, "hours": [{"weekday": 1, "opensAt": "12:00", "closesAt": "08:00"}]},
        {"perDepartment": False, "hours": [{"weekday": 8, "opensAt": "08:00", "closesAt": "12:00"}]},
        {"perDepartment": False, "hours": [
            {"weekday": 1, "opensAt": "08:00", "closesAt": "12:00", "secondOpensAt": "11:00", "secondClosesAt": "18:00"}
        ]},
        {"perDepartment": False, "hours": [
            {"weekday": 1, "opensAt": "08:00", "closesAt": "12:00"},
            {"weekday": 1, "opensAt": "13:00", "closesAt": "18:00"},
        ]},
    ],
)
def test_hours_that_cannot_be_true_are_refused(client, body):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    assert _put_hours(client, dealership["id"], location, body).status_code == 422


# --- closures ------------------------------------------------------------------------------------------


def test_closures_are_added_changed_and_removed_with_if_match(client):
    dealership = _create_dealership(client)
    location = _create(client, dealership["id"])
    url = f"/v1/dealerships/{dealership['id']}/locations/{location['id']}/closures"
    created = client.post(
        url,
        json={"startsOn": "2026-07-20", "endsOn": "2026-08-07", "reason": "Betriebsferien"},
        headers=_manager(dealership["id"]),
    )
    assert created.status_code == 201, created.text
    closure = created.json()
    inverted = client.post(
        url, json={"startsOn": "2026-08-07", "endsOn": "2026-07-20", "reason": "x"}, headers=_manager(dealership["id"])
    )
    assert inverted.status_code == 422
    sales = _role(dealership["id"], AccessRole.SALES)
    assert client.post(url, json={"startsOn": "2026-12-24", "endsOn": "2026-12-24", "reason": "x"}, headers=sales).status_code == 403
    assert [c["id"] for c in client.get(url, headers=sales).json()["items"]] == [closure["id"]]

    item = f"{url}/{closure['id']}"
    changed = client.patch(
        item, json={"endsOn": "2026-08-14"}, headers={**_manager(dealership["id"]), "If-Match": str(closure["version"])}
    )
    assert changed.status_code == 200 and changed.json()["endsOn"] == "2026-08-14"
    backwards = client.patch(
        item, json={"endsOn": "2026-07-01"}, headers={**_manager(dealership["id"]), "If-Match": str(changed.json()["version"])}
    )
    assert backwards.status_code == 422
    stale = client.delete(item, headers={**_manager(dealership["id"]), "If-Match": str(closure["version"])})
    assert stale.status_code == 409
    removed = client.delete(item, headers={**_manager(dealership["id"]), "If-Match": str(changed.json()["version"])})
    assert removed.status_code == 204
    assert client.get(url, headers=sales).json()["items"] == []


# --- holiday calendars over the API ------------------------------------------------------------------------


def test_any_user_reads_the_calendars_and_a_years_dates(client, db_session):
    calendars = seed_holiday_calendars(db_session)
    dealership = _create_dealership(client)
    headers = _role(dealership["id"], AccessRole.TECHNICIAN)
    listed = client.get("/v1/holiday-calendars", headers=headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == len(CALENDARS)
    so = next(c for c in items if c["code"] == "SO")
    labour = next(h for h in so["holidays"] if h["code"] == "labour_day")
    assert labour["afternoonOnly"] is True and labour["rule"] == "fixed_date"

    dates = client.get(f"/v1/holiday-calendars/{calendars['GL'].id}/dates?year=2026", headers=headers)
    assert dates.status_code == 200
    by_code = {d["code"]: d["date"] for d in dates.json()["items"]}
    assert by_code["naefelser_fahrt"] == "2026-04-09"
    assert [d["date"] for d in dates.json()["items"]] == sorted(d["date"] for d in dates.json()["items"])
    assert client.get(f"/v1/holiday-calendars/{calendars['GL'].id}/dates?year=1800", headers=headers).status_code == 422
    assert client.get(f"/v1/holiday-calendars/{uuid.uuid4()}/dates", headers=headers).status_code == 404
    assert client.get("/v1/holiday-calendars").status_code == 401


# --- user_location (FR-A-02) ----------------------------------------------------------------------------------


def test_a_deactivated_location_keeps_its_users_but_takes_no_new_ones(client):
    from tests.identity_support import create_user

    dealership = _create_dealership(client)
    [first] = _list(client, dealership["id"])
    second = _create(client, dealership["id"])
    manager = create_user(client, dealership["id"])
    headers = _bearer(
        _token(tenant_id=uuid.UUID(dealership["id"]), user_id=uuid.UUID(manager["id"]), is_dealer_manager=True)
    )
    user = create_user(client, dealership["id"], isDealerManager=False, primaryLocationId=second["id"])
    assert _patch(client, dealership["id"], second, {"isActive": False}).status_code == 200

    other = create_user(client, dealership["id"], isDealerManager=False, primaryLocationId=first["id"])
    refused = client.patch(
        f"/v1/dealerships/{dealership['id']}/users/{other['id']}",
        json={"additionalLocationIds": [second["id"]]},
        headers={**headers, "If-Match": str(other["version"])},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == "location_inactive"
    kept = client.patch(
        f"/v1/dealerships/{dealership['id']}/users/{user['id']}",
        json={"additionalLocationIds": [first["id"]]},
        headers={**headers, "If-Match": str(user["version"])},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["primaryLocationId"] == second["id"]
