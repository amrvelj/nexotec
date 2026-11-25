"""KAN-805 PR-2 (ADR-214, ADR-216): the settings store's API in Dealer
Administration — areas, one area's settings, writes for the Betrieb and a
location, the history, restore; who may do what; what is not found."""

import uuid

import pytest
from sqlalchemy import select

from app.core.permissions import ModuleCode
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from tests import setting_store_support as samples
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership, platform_admin_headers
from tests.platform_tenant_support import ensure_platform_tenant


@pytest.fixture()
def declared():
    with samples.declared(*samples.SAMPLES):
        yield


def _betrieb(client, db, *, aftersales: bool = True):
    dealership_id = uuid.UUID(create_dealership(client))
    if aftersales:
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
        db.commit()
    boss = s.manager(client, db, dealership_id)
    clerk = s.manager(client, db, dealership_id, isDealerManager=False)
    return dealership_id, s.headers_for(boss, dealership_id), s.headers_for(clerk, dealership_id, manager=False)


def _base(dealership_id) -> str:
    return f"/v1/dealerships/{dealership_id}"


def _put(client, headers, dealership_id, key, value, version, location_id=None):
    path = f"{_base(dealership_id)}/setting-values/{key}"
    if location_id is not None:
        path += f"/locations/{location_id}"
    return client.put(path, json={"value": value}, headers={**headers, "If-Match": str(version)})


def _seat(db, dealership_id) -> uuid.UUID:
    return db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))


def test_the_areas_a_betrieb_sees_follow_its_modules(client, db_session, declared):
    with_aftersales, boss, _ = _betrieb(client, db_session)
    without, boss_without, _ = _betrieb(client, db_session, aftersales=False)

    seen = client.get(f"{_base(with_aftersales)}/setting-areas", headers=boss)
    assert seen.status_code == 200, seen.text
    assert seen.json()["items"] == [{"area": "testarea", "settingCount": 5}]
    # Only the setting of no module: the desk note.
    assert client.get(f"{_base(without)}/setting-areas", headers=boss_without).json()["items"] == [
        {"area": "testarea", "settingCount": 1}
    ]
    hidden = _put(client, boss_without, without, samples.WAIT.key, {"number": "5"}, 0)
    assert hidden.status_code == 404
    assert client.get(f"{_base(without)}/setting-values/{samples.WAIT.key}/history", headers=boss_without).status_code == 404


def test_one_area_lists_declarations_and_every_slot(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": "15"}, 0).status_code == 200

    response = client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=boss)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["showsLocations"] is False  # one location, no location value
    by_key = {item["declaration"]["key"]: item for item in body["settings"]}
    wait = by_key[samples.WAIT.key]
    assert wait["declaration"] | {} == {
        **wait["declaration"],
        "kind": "number",
        "whenNotSet": "quiet",
        "unit": "minutes",
        "checklistItem": True,
        "maximum": "240",
    }
    assert (wait["dealership"]["isSet"], wait["dealership"]["value"]["number"], wait["dealership"]["version"]) == (
        True, "15", 1,
    )
    unit = by_key[samples.UNIT.key]
    assert unit["declaration"]["actsAs"]["choice"] == "hours"
    assert (unit["dealership"]["isSet"], unit["dealership"]["version"]) == (False, 0)
    assert client.get(f"{_base(dealership_id)}/setting-areas/nowhere", headers=boss).status_code == 404


def test_the_location_column_shows_with_two_active_locations_or_a_location_value(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    closed = samples.add_location(db_session, dealership_id, name="Geschlossen", active=True)
    assert client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=boss).json()["showsLocations"] is True

    assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": "30"}, 0, location_id=closed).status_code == 200
    location = db_session.get(Location, closed)
    location.is_active = False
    db_session.commit()
    # One active location left, but a location holds a value of its own (Anto, 2026-11-24).
    body = client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=boss).json()
    assert body["showsLocations"] is True
    wait = next(item for item in body["settings"] if item["declaration"]["key"] == samples.WAIT.key)
    assert [(slot["locationId"], slot["value"]["number"]) for slot in wait["locations"]] == [(str(closed), "30")]


def test_a_write_takes_the_slots_version_and_refuses_what_does_not_fit(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    key = samples.WAIT.key

    no_if_match = client.put(f"{_base(dealership_id)}/setting-values/{key}", json={"value": {"number": "5"}}, headers=boss)
    assert no_if_match.status_code == 400
    first = _put(client, boss, dealership_id, key, {"number": "5"}, 0)
    assert (first.status_code, first.json()["version"]) == (200, 1)
    assert _put(client, boss, dealership_id, key, {"number": "6"}, 0).status_code == 409

    for value, reason in (
        ({"switch": True}, "setting_value_wrong_field"),
        ({"number": "5", "choice": "hours"}, "setting_value_wrong_field"),
        ({}, "setting_value_wrong_field"),
        ({"number": "241"}, "setting_value_above_maximum"),
        ({"number": "1.5"}, "setting_value_too_many_decimals"),
    ):
        refused = _put(client, boss, dealership_id, key, value, 1)
        assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (422, reason), value
    assert _put(client, boss, dealership_id, samples.UNIT.key, {"choice": "days"}, 0).json()["error"]["details"][
        "reason"
    ] == "setting_value_not_a_choice"

    cleared = _put(client, boss, dealership_id, key, None, 1)
    assert (cleared.status_code, cleared.json()["isSet"], cleared.json()["value"], cleared.json()["version"]) == (
        200, False, None, 2,
    )


def test_a_text_and_a_switch_round_trip(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    text = _put(client, boss, dealership_id, samples.NOTE.key, {"text": {"name": "Schlüssel am Empfang", "translations": {"fr": "Clés à la réception"}}}, 0)
    assert text.json()["value"]["text"] == {"name": "Schlüssel am Empfang", "translations": {"fr": "Clés à la réception"}}
    switch = _put(client, boss, dealership_id, samples.SWITCH.key, {"switch": False}, 0)
    assert (switch.status_code, switch.json()["value"]["switch"]) == (200, False)


def test_only_the_manager_writes_and_every_role_reads(client, db_session, declared):
    dealership_id, boss, clerk = _betrieb(client, db_session)
    assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": "5"}, 0).status_code == 200

    assert _put(client, clerk, dealership_id, samples.WAIT.key, {"number": "6"}, 1).status_code == 403
    assert client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=clerk).status_code == 200
    history = client.get(f"{_base(dealership_id)}/setting-values/{samples.WAIT.key}/history", headers=clerk)
    assert (history.status_code, len(history.json()["items"])) == (200, 1)
    change_id = history.json()["items"][0]["id"]
    restore = client.post(
        f"{_base(dealership_id)}/setting-changes/{change_id}/restore", headers={**clerk, "If-Match": "1"}
    )
    assert restore.status_code == 403


def test_another_betriebs_path_location_and_change_are_not_found(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    other, other_boss, _ = _betrieb(client, db_session)
    assert _put(client, other_boss, other, samples.WAIT.key, {"number": "9"}, 0).status_code == 200
    theirs = client.get(f"{_base(other)}/setting-values/{samples.WAIT.key}/history", headers=other_boss).json()["items"][0]

    assert client.get(f"{_base(other)}/setting-areas/testarea", headers=boss).status_code == 404
    assert _put(client, boss, other, samples.WAIT.key, {"number": "1"}, 1).status_code == 404
    assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": "1"}, 0, location_id=_seat(db_session, other)).status_code == 404
    assert client.get(
        f"{_base(dealership_id)}/setting-values/{samples.WAIT.key}/history",
        params={"locationId": str(_seat(db_session, other))}, headers=boss,
    ).status_code == 404
    assert client.post(
        f"{_base(dealership_id)}/setting-changes/{theirs['id']}/restore", headers={**boss, "If-Match": "0"}
    ).status_code == 404


def test_a_deactivated_locations_value_is_refused(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    closed = samples.add_location(db_session, dealership_id, active=False)
    refused = _put(client, boss, dealership_id, samples.WAIT.key, {"number": "5"}, 0, location_id=closed)
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (409, "setting_location_inactive")


def test_the_history_is_newest_first_in_pages_and_a_restore_is_a_change(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    key = samples.WAIT.key
    for version, value in enumerate(("10", "20", "30")):
        assert _put(client, boss, dealership_id, key, {"number": value}, version).status_code == 200

    first_page = client.get(f"{_base(dealership_id)}/setting-values/{key}/history", params={"limit": 2}, headers=boss).json()
    assert [item["value"]["number"] for item in first_page["items"]] == ["30", "20"]
    second_page = client.get(
        f"{_base(dealership_id)}/setting-values/{key}/history",
        params={"limit": 2, "cursor": first_page["nextCursor"]}, headers=boss,
    ).json()
    assert ([item["value"]["number"] for item in second_page["items"]], second_page["nextCursor"]) == (["10"], None)

    oldest = second_page["items"][0]["id"]
    headers = {**boss, "If-Match": "3", "Idempotency-Key": str(uuid.uuid4())}
    restored = client.post(f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers=headers)
    assert (restored.status_code, restored.json()["value"]["number"], restored.json()["version"]) == (200, "10", 4)
    again = client.post(f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers=headers)
    assert (again.status_code, again.json()["version"]) == (200, 4)  # the same key replays, nothing new

    newest = client.get(f"{_base(dealership_id)}/setting-values/{key}/history", headers=boss).json()["items"][0]
    assert (newest["origin"], newest["restoredFromId"], newest["versionAfter"]) == ("restore", oldest, 4)
    stale = client.post(f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers={**boss, "If-Match": "3"})
    assert stale.status_code == 409


# --- review fixes (KAN-805 PR-2) ----------------------------------------------------


def test_a_value_that_no_longer_fits_shows_as_not_set_and_says_why(client, db_session):
    wide = samples.SettingDeclaration(
        key="testarea.unit", module=None, kind=samples.SettingKind.CHOICE, when_not_set=samples.WhenNotSet.QUIET,
        choices=("hours", "work_units"),
    )
    with samples.declared(wide):
        dealership_id, boss, _ = _betrieb(client, db_session)
        assert _put(client, boss, dealership_id, wide.key, {"choice": "work_units"}, 0).status_code == 200
    narrow = samples.SettingDeclaration(
        key="testarea.unit", module=None, kind=samples.SettingKind.CHOICE, when_not_set=samples.WhenNotSet.QUIET,
        choices=("hours", "days"),
    )
    with samples.declared(narrow):
        body = client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=boss).json()
        slot = body["settings"][0]["dealership"]
        assert (slot["isSet"], slot["value"], slot["fitsDeclaration"], slot["version"]) == (False, None, False, 1)
        # Set again: it fits once more.
        again = _put(client, boss, dealership_id, narrow.key, {"choice": "days"}, 1)
        assert (again.json()["isSet"], again.json()["fitsDeclaration"]) == (True, True)


def test_a_number_travels_as_a_decimal_string_and_json_types_are_strict(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    key = samples.FEE.key
    stored = _put(client, boss, dealership_id, key, {"number": "99999999999999.99"}, 0)
    assert stored.json()["value"]["number"] == "99999999999999.99"  # exact: a string, never a float
    too_large = _put(client, boss, dealership_id, key, {"number": "100000000000000"}, 1)
    assert too_large.json()["error"]["details"]["reason"] == "setting_value_too_large"

    assert _put(client, boss, dealership_id, key, {"number": 12}, 1).status_code == 422  # a JSON number
    bad = _put(client, boss, dealership_id, key, {"number": "zwölf"}, 1)
    assert bad.json()["error"]["details"]["reason"] == "setting_value_not_a_number"
    assert _put(client, boss, dealership_id, samples.SWITCH.key, {"switch": "yes"}, 0).status_code == 422
    long_choice = _put(client, boss, dealership_id, samples.UNIT.key, {"choice": "x" * 100}, 0)
    assert long_choice.json()["error"]["details"]["reason"] == "setting_value_not_a_choice"


def test_a_restore_into_a_location_slot_takes_that_slots_version(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    seat = _seat(db_session, dealership_id)
    for version, value in enumerate(("10", "20")):
        assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": value}, version, location_id=seat).status_code == 200
    history = client.get(
        f"{_base(dealership_id)}/setting-values/{samples.WAIT.key}/history", params={"locationId": str(seat)}, headers=boss
    ).json()["items"]
    oldest = history[-1]["id"]
    assert client.post(f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers={**boss, "If-Match": "0"}).status_code == 409
    restored = client.post(f"{_base(dealership_id)}/setting-changes/{oldest}/restore", headers={**boss, "If-Match": "2"})
    assert (restored.status_code, restored.json()["locationId"], restored.json()["value"]["number"]) == (200, str(seat), "10")
    assert client.get(f"{_base(dealership_id)}/setting-values/{samples.WAIT.key}/history", headers=boss).json()["items"] == []


def test_a_restore_of_a_module_the_betrieb_no_longer_holds_is_not_found(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    assert _put(client, boss, dealership_id, samples.WAIT.key, {"number": "5"}, 0).status_code == 200
    change = client.get(f"{_base(dealership_id)}/setting-values/{samples.WAIT.key}/history", headers=boss).json()["items"][0]
    for row in db_session.scalars(select(ModuleEntitlement).where(ModuleEntitlement.dealership_id == dealership_id)):
        db_session.delete(row)
    db_session.commit()

    assert client.post(
        f"{_base(dealership_id)}/setting-changes/{change['id']}/restore", headers={**boss, "If-Match": "1"}
    ).status_code == 404


def test_every_role_reads_the_areas_and_the_platform_tenant_has_none(client, db_session, declared):
    dealership_id, _, clerk = _betrieb(client, db_session)
    assert client.get(f"{_base(dealership_id)}/setting-areas", headers=clerk).status_code == 200

    platform_id = uuid.UUID(ensure_platform_tenant(client))
    staff = platform_admin_headers(tenant_id=platform_id)
    assert client.get(f"{_base(platform_id)}/setting-areas", headers=staff).status_code == 404


@pytest.mark.parametrize("text", ["1e1000000", " 5", "5 ", "1_0", "+5", "1e1", "\u0661\u0665", "5.", ".5", "", "--5"])
def test_only_a_plain_decimal_string_is_a_number(client, db_session, declared, text):
    dealership_id, boss, _ = _betrieb(client, db_session)
    refused = _put(client, boss, dealership_id, samples.FEE.key, {"number": text}, 0)
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (422, "setting_value_not_a_number")


def test_minus_zero_is_zero(client, db_session, declared):
    dealership_id, boss, _ = _betrieb(client, db_session)
    zero = _put(client, boss, dealership_id, samples.WAIT.key, {"number": "-0"}, 0)
    assert zero.json()["value"]["number"] == "0"


def test_a_huge_exponent_never_overflows_the_declaration_check(db_session):
    from decimal import Decimal

    from app.platform.organisation.services.setting_declarations import SettingValueError, validate_value

    with pytest.raises(SettingValueError) as refused:
        validate_value(samples.FEE, Decimal("1e1000000"))
    assert refused.value.reason == "setting_value_too_large"


def test_a_stale_location_value_shows_the_location_column(client, db_session):
    wide = samples.SettingDeclaration(
        key="testarea.unit", module=None, kind=samples.SettingKind.CHOICE, when_not_set=samples.WhenNotSet.QUIET,
        choices=("hours", "work_units"),
    )
    with samples.declared(wide):
        dealership_id, boss, _ = _betrieb(client, db_session)
        assert _put(client, boss, dealership_id, wide.key, {"choice": "work_units"}, 0, location_id=_seat(db_session, dealership_id)).status_code == 200
    narrow = samples.SettingDeclaration(
        key="testarea.unit", module=None, kind=samples.SettingKind.CHOICE, when_not_set=samples.WhenNotSet.QUIET,
        choices=("hours",),
    )
    with samples.declared(narrow):
        body = client.get(f"{_base(dealership_id)}/setting-areas/testarea", headers=boss).json()
        assert body["showsLocations"] is True  # one active location, but a value of its own (stale)
        assert body["settings"][0]["locations"][0]["fitsDeclaration"] is False
