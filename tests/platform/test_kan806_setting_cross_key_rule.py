"""KAN-806 (check 49; ruling 183; ADR-216 (5)): a rule across two settings.

A choice may need another setting in force: O18 «work units» needs O19
«work units per hour». Proved here on `testarea` samples of the same shape,
so the store's rule is tested apart from the labour-rate declarations
(`test_kan806_labour_rate_settings.py` checks the real keys).
"""

import uuid
from decimal import Decimal

import pytest

from app.core.permissions import ModuleCode
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    SettingValueError,
    WhenNotSet,
    check_declaration_references,
)
from tests import setting_store_support as s
from tests import setup_checklist_support as checklist
from tests.identity_support import create_dealership

UNIT = SettingDeclaration(
    key="testarea.ruled_unit",
    module="aftersales",
    kind=SettingKind.CHOICE,
    when_not_set=WhenNotSet.ACTS_AS,
    acts_as="hours",
    choices=("hours", "work_units"),
    choice_requires=(("work_units", "testarea.units_per_hour"),),
)
PER_HOUR = SettingDeclaration(
    key="testarea.units_per_hour",
    module="aftersales",
    kind=SettingKind.NUMBER,
    when_not_set=WhenNotSet.QUIET,
    minimum=Decimal(1),
    maximum=Decimal(100),
)


@pytest.fixture()
def ruled():
    with s.declared(UNIT, PER_HOUR):
        yield


@pytest.fixture()
def betrieb(client):
    return uuid.UUID(create_dealership(client))


def _change(db, dealership_id, key, value, *, location_id=None):
    version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    slot = store.change_setting(
        db, dealership_id=dealership_id, location_id=location_id, key=key, value=value,
        expected_version=version, actor_id=None,
    )
    db.commit()
    return slot


def _refused(db, dealership_id, key, value, *, location_id=None) -> SettingValueError:
    with pytest.raises(SettingValueError) as caught:
        _change(db, dealership_id, key, value, location_id=location_id)
    db.rollback()
    return caught.value


def test_work_units_are_refused_without_work_units_per_hour(ruled, betrieb, db_session):
    refusal = _refused(db_session, betrieb, UNIT.key, "work_units")
    assert refusal.reason == "setting_choice_requires_setting"
    assert refusal.details == {
        "setting": UNIT.key, "choice": "work_units", "requires": PER_HOUR.key, "locationId": None,
    }
    assert store.get_slot(db_session, dealership_id=betrieb, location_id=None, key=UNIT.key).version == 0


def test_work_units_are_accepted_once_work_units_per_hour_is_set(ruled, betrieb, db_session):
    _change(db_session, betrieb, PER_HOUR.key, Decimal(12))
    slot = _change(db_session, betrieb, UNIT.key, "work_units")
    assert slot.value == "work_units"


def test_hours_need_nothing(ruled, betrieb, db_session):
    assert _change(db_session, betrieb, UNIT.key, "hours").value == "hours"


def test_work_units_per_hour_cannot_be_cleared_while_work_units_stand(ruled, betrieb, db_session):
    _change(db_session, betrieb, PER_HOUR.key, Decimal(12))
    _change(db_session, betrieb, UNIT.key, "work_units")
    refusal = _refused(db_session, betrieb, PER_HOUR.key, store.NOT_SET)
    assert refusal.reason == "setting_required_by_choice"
    assert refusal.details["setting"] == UNIT.key
    # Back to hours, and it can be cleared.
    _change(db_session, betrieb, UNIT.key, "hours")
    assert _change(db_session, betrieb, PER_HOUR.key, store.NOT_SET).is_set is False


def test_a_location_on_work_units_takes_the_betriebs_work_units_per_hour(ruled, betrieb, db_session):
    north = s.add_location(db_session, betrieb)
    _change(db_session, betrieb, PER_HOUR.key, Decimal(10))
    assert _change(db_session, betrieb, UNIT.key, "work_units", location_id=north).value == "work_units"
    # The Betrieb's value is the one that location counts on: clearing it is refused there.
    refusal = _refused(db_session, betrieb, PER_HOUR.key, store.NOT_SET)
    assert refusal.reason == "setting_required_by_choice"
    assert refusal.details["locationId"] == str(north)


def test_a_location_on_work_units_without_any_work_units_per_hour_is_refused(ruled, betrieb, db_session):
    north = s.add_location(db_session, betrieb)
    refusal = _refused(db_session, betrieb, UNIT.key, "work_units", location_id=north)
    assert refusal.reason == "setting_choice_requires_setting"
    assert refusal.details["locationId"] == str(north)


def test_the_betrieb_on_work_units_with_only_a_locations_value_is_refused(ruled, betrieb, db_session):
    north = s.add_location(db_session, betrieb)
    _change(db_session, betrieb, PER_HOUR.key, Decimal(12), location_id=north)
    # The Betrieb's own scope (a read without a location) would have no work units per hour.
    refusal = _refused(db_session, betrieb, UNIT.key, "work_units")
    assert refusal.details["locationId"] is None
    # At that location it is fine.
    assert _change(db_session, betrieb, UNIT.key, "work_units", location_id=north).value == "work_units"


def test_an_inactive_location_does_not_hold_the_betrieb_back(ruled, betrieb, db_session):
    north = s.add_location(db_session, betrieb)
    _change(db_session, betrieb, PER_HOUR.key, Decimal(12))
    _change(db_session, betrieb, UNIT.key, "work_units", location_id=north)
    from app.platform.organisation.models.dealership import Location

    db_session.get(Location, north).is_active = False
    db_session.commit()
    assert _change(db_session, betrieb, PER_HOUR.key, store.NOT_SET).is_set is False


def test_a_restore_is_checked_too(ruled, betrieb, db_session):
    _change(db_session, betrieb, PER_HOUR.key, Decimal(12))
    _change(db_session, betrieb, UNIT.key, "work_units")
    _change(db_session, betrieb, UNIT.key, "hours")
    _change(db_session, betrieb, PER_HOUR.key, store.NOT_SET)
    history = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=UNIT.key)
    work_units_change = next(change for change in history if change.value == "work_units")
    with pytest.raises(SettingValueError) as caught:
        store.restore_change(
            db_session, dealership_id=betrieb, change_id=work_units_change.id,
            expected_version=store.get_slot(db_session, dealership_id=betrieb, location_id=None, key=UNIT.key).version,
            actor_id=None,
        )
    assert caught.value.reason == "setting_choice_requires_setting"


def test_a_rule_naming_no_choice_is_refused_at_declaration(ruled):
    bad = SettingDeclaration(
        key="testarea.bad_rule", module="aftersales", kind=SettingKind.CHOICE, when_not_set=WhenNotSet.QUIET,
        choices=("a", "b"), choice_requires=(("c", PER_HOUR.key),),
    )
    with pytest.raises(SettingDeclarationError), s.declared(bad):
        pass


def test_a_rule_requiring_an_undeclared_key_is_refused_at_start_up():
    lonely = SettingDeclaration(
        key="testarea.lonely_unit", module="aftersales", kind=SettingKind.CHOICE, when_not_set=WhenNotSet.QUIET,
        choices=("hours", "work_units"), choice_requires=(("work_units", "testarea.nobody"),),
    )
    with s.declared(lonely), pytest.raises(SettingDeclarationError):
        check_declaration_references()


def test_the_area_api_names_the_rule_and_answers_the_refusal(ruled, client, db_session):
    dealership_id = uuid.UUID(create_dealership(client))
    db_session.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
    db_session.commit()
    headers = checklist.headers_for(checklist.manager(client, db_session, dealership_id), dealership_id)
    area = client.get(f"/v1/dealerships/{dealership_id}/setting-areas/testarea", headers=headers)
    assert area.status_code == 200, area.text
    unit = next(item for item in area.json()["settings"] if item["declaration"]["key"] == UNIT.key)
    assert unit["declaration"]["choiceRequires"] == [{"choice": "work_units", "key": PER_HOUR.key}]
    put = client.put(
        f"/v1/dealerships/{dealership_id}/setting-values/{UNIT.key}",
        json={"value": {"choice": "work_units"}},
        headers={**headers, "If-Match": "0"},
    )
    assert put.status_code == 422, put.text
    details = put.json()["error"]["details"]
    assert details["reason"] == "setting_choice_requires_setting"
    assert details["requires"] == PER_HOUR.key
