"""KAN-808 (Anto, 2026-11-25): a number declared `multiple_of` another — O7,
the planner's slot length, of O6, its grid — is a whole multiple of it wherever
both are in force. The settings store refuses writing either to break it, for
the Betrieb and each active location holding a slot of either key, under the
Betrieb's settings lock (`setting_rules`, as check 49); «not set» on either
side breaks nothing; the copy from a sister writes O6 before O7; reconciliation
reports a scope a reactivated location left broken."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.setting_registry  # noqa: F401 — registers O6 and O7
from app.core.reconciliation import find_rows_in_state
from app.platform import reconciliation
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.setting_value import SettingValue
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    SettingValueError,
    WhenNotSet,
    check_declaration_references,
    declared_settings_in_write_order,
)
from tests import setting_store_support as samples
from tests.identity_support import create_dealership
from tests.platform.test_configuration_copy import _copy
from tests.platform.test_kan805_settings_copy import SETTINGS, _aftersales_pair, _items
from tests.platform.test_kan805_settings_copy import _set as _set_in
from tests.platform.test_kan805_settings_copy import _value as _value_in

GRID = "planning.grid_minutes"
SLOT = "planning.slot_minutes"
_CHECK = "setting_value a number declared a multiple of another setting that is no multiple of it"


@pytest.fixture()
def betrieb(client):
    return uuid.UUID(create_dealership(client))


def _set(db, dealership_id, key, minutes, location_id=None):
    version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    store.change_setting(
        db, dealership_id=dealership_id, location_id=location_id, key=key,
        value=store.NOT_SET if minutes is None else Decimal(minutes), expected_version=version, actor_id=None,
    )
    db.commit()


def _refused(db, dealership_id, key, minutes, location_id=None) -> SettingValueError:
    with pytest.raises(SettingValueError) as caught:
        _set(db, dealership_id, key, minutes, location_id)
    db.rollback()
    return caught.value


def test_o7_is_declared_a_multiple_of_o6():
    from app.planning.services.planner_settings import SLOT_MINUTES

    assert SLOT_MINUTES.multiple_of == GRID


def test_a_slot_length_that_is_no_multiple_of_the_grid_is_refused(db_session, betrieb):
    _set(db_session, betrieb, GRID, 15)
    refused = _refused(db_session, betrieb, SLOT, 20)
    assert (refused.reason, refused.details) == (
        "setting_not_a_multiple_of_setting", {"setting": SLOT, "multipleOf": GRID, "locationId": None},
    )
    _set(db_session, betrieb, SLOT, 45)
    assert store.read_setting(db_session, dealership_id=betrieb, location_id=None, key=SLOT).value == Decimal(45)


def test_a_grid_that_no_longer_divides_the_slot_length_is_refused(db_session, betrieb):
    _set(db_session, betrieb, GRID, 15)
    _set(db_session, betrieb, SLOT, 30)
    refused = _refused(db_session, betrieb, GRID, 20)
    assert (refused.reason, refused.details) == (
        "setting_no_longer_divides_setting", {"setting": SLOT, "multipleOf": GRID, "locationId": None},
    )
    _set(db_session, betrieb, GRID, 10)  # 30 is a multiple of 10
    _set(db_session, betrieb, GRID, None)  # clearing breaks nothing: O6 refuses its readers while not set


def test_not_set_on_either_side_breaks_nothing(db_session, betrieb):
    _set(db_session, betrieb, SLOT, 25)  # O6 not set
    refused = _refused(db_session, betrieb, GRID, 10)  # now both in force: 25 is no multiple of 10
    assert refused.reason == "setting_no_longer_divides_setting"
    _set(db_session, betrieb, GRID, 5)
    _set(db_session, betrieb, SLOT, None)
    _set(db_session, betrieb, GRID, 30)


def test_each_location_where_both_are_in_force_is_a_scope(db_session, betrieb):
    north = samples.add_location(db_session, betrieb)
    _set(db_session, betrieb, GRID, 15)
    _set(db_session, betrieb, SLOT, 30)
    # North's own grid against the Betrieb's slot length.
    refused = _refused(db_session, betrieb, GRID, 20, location_id=north)
    assert (refused.reason, refused.details["locationId"]) == ("setting_no_longer_divides_setting", str(north))
    # North's own slot length against the Betrieb's grid.
    refused = _refused(db_session, betrieb, SLOT, 50, location_id=north)
    assert (refused.reason, refused.details["locationId"]) == ("setting_not_a_multiple_of_setting", str(north))
    # North's own slot length first (60 of the Betrieb's 15), then its own grid (60 of 20).
    _set(db_session, betrieb, SLOT, 60, location_id=north)
    _set(db_session, betrieb, GRID, 20, location_id=north)
    # The Betrieb's grid must still divide the Betrieb's own slot length; north has its own pair.
    refused = _refused(db_session, betrieb, GRID, 20)
    assert (refused.reason, refused.details["locationId"]) == ("setting_no_longer_divides_setting", None)
    _set(db_session, betrieb, GRID, 10)


def test_a_rule_naming_an_undeclared_or_unfit_key_is_refused_at_start_up():
    multiple = SettingDeclaration(
        key="testarea.slot", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
        minimum=Decimal(5), multiple_of="testarea.grid",
    )
    with samples.declared(multiple), pytest.raises(SettingDeclarationError, match="nobody declared"):
        check_declaration_references()
    for unfit in (
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET),
        SettingDeclaration(key="testarea.grid", module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET),
        SettingDeclaration(key="testarea.grid", module="parts", kind=SettingKind.NUMBER,
                           when_not_set=WhenNotSet.QUIET, minimum=Decimal(1)),
    ):
        with samples.declared(multiple, unfit), pytest.raises(SettingDeclarationError):
            check_declaration_references()
    for wrong in (
        SettingDeclaration(key="testarea.slot", module=None, kind=SettingKind.AMOUNT, when_not_set=WhenNotSet.QUIET,
                           multiple_of="testarea.grid"),
        SettingDeclaration(key="testarea.slot", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
                           multiple_of="testarea.slot"),
    ):
        with pytest.raises(SettingDeclarationError), samples.declared(wrong):
            pass


def test_the_key_a_number_is_a_multiple_of_is_written_first():
    """A multiple that sorts before its divisor is still written after it."""

    multiple = SettingDeclaration(
        key="testarea.a_slot", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
        minimum=Decimal(5), multiple_of="testarea.z_grid",
    )
    divisor = SettingDeclaration(
        key="testarea.z_grid", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET, minimum=Decimal(5)
    )
    with samples.declared(multiple, divisor):
        order = [d.key for d in declared_settings_in_write_order() if d.key.startswith("testarea.")]
    assert order == ["testarea.z_grid", "testarea.a_slot"]
    keys = [d.key for d in declared_settings_in_write_order()]
    assert keys.index(GRID) < keys.index(SLOT)


def test_the_copy_writes_the_grid_before_the_slot_length(client, engine):
    """Target O6 20 / O7 40, source O6 10 / O7 30: the grid first (40 is a
    multiple of 10), then the slot length (30 of 10). The other way round, 30
    would be refused against the target's 20."""

    source, target, _, headers = _aftersales_pair(client, engine)
    _set_in(engine, target, GRID, Decimal(20))
    _set_in(engine, target, SLOT, Decimal(40))
    _set_in(engine, source, GRID, Decimal(10))
    _set_in(engine, source, SLOT, Decimal(30))
    expected = {GRID: ("overwritten", None), SLOT: ("overwritten", None)}
    assert _items(_copy(client, target, headers, source, dry_run=True, subsets=SETTINGS)) == expected
    assert _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)) == expected
    assert (_value_in(engine, target, GRID), _value_in(engine, target, SLOT)) == (Decimal(10), Decimal(30))


def test_the_copy_writes_a_pair_that_fits_neither_order_as_one_change(client, engine):
    """KAN-839: target O6 15 / O7 45, source O6 10 / O7 40. One at a time,
    O6=10 is refused (45 is no multiple of 10) and then O7=40 (40 is no
    multiple of 15) — although 10/40 is valid. Judged together, both land, each
    with its own history row of origin copy."""

    from app.platform.organisation.models.setting_value import SettingChangeOrigin, SettingValueChange
    from tests.platform.test_kan805_settings_copy import _session

    source, target, _, headers = _aftersales_pair(client, engine)
    _set_in(engine, target, GRID, Decimal(15))
    _set_in(engine, target, SLOT, Decimal(45))
    _set_in(engine, source, GRID, Decimal(10))
    _set_in(engine, source, SLOT, Decimal(40))
    expected = {GRID: ("overwritten", None), SLOT: ("overwritten", None)}
    assert _items(_copy(client, target, headers, source, dry_run=True, subsets=SETTINGS)) == expected
    assert (_value_in(engine, target, GRID), _value_in(engine, target, SLOT)) == (Decimal(15), Decimal(45))
    assert _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)) == expected
    assert (_value_in(engine, target, GRID), _value_in(engine, target, SLOT)) == (Decimal(10), Decimal(40))
    with _session(engine) as db:
        copied = db.scalars(
            select(SettingValueChange.key).where(
                SettingValueChange.dealership_id == uuid.UUID(target),
                SettingValueChange.origin == SettingChangeOrigin.COPY,
            )
        ).all()
    assert sorted(copied) == sorted([GRID, SLOT])


def test_a_pair_the_target_refuses_as_a_whole_is_tried_key_by_key(client, engine):
    """KAN-839: the pair is judged against every scope of the target. A target
    location's own O7 45 breaks a copied grid of 10 there, so the pair is
    refused as a whole; each key is then tried alone, as before: the grid is
    refused (it no longer divides the location's 45), the slot length 30 fits
    the target's grid of 15 and lands."""

    from tests.platform.test_kan805_settings_copy import _session

    source, target, _, headers = _aftersales_pair(client, engine)
    with _session(engine) as db:
        location_id = db.scalar(select(Location.id).where(Location.tenant_id == uuid.UUID(target)))
    _set_in(engine, target, GRID, Decimal(15))
    _set_in(engine, target, SLOT, Decimal(45))
    _set_in(engine, target, SLOT, Decimal(45), location_id=location_id)
    _set_in(engine, source, GRID, Decimal(10))
    _set_in(engine, source, SLOT, Decimal(30))
    expected = {GRID: ("skipped", "setting_no_longer_divides_setting"), SLOT: ("overwritten", None)}
    assert _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)) == expected
    assert (_value_in(engine, target, GRID), _value_in(engine, target, SLOT)) == (Decimal(15), Decimal(30))


def test_the_store_judges_a_joint_change_on_the_state_after_all_of_it(db_session, betrieb):
    """KAN-839: `change_settings` checks the rule once, after every slot of the
    change — and refuses before writing anything when the result breaks it."""

    _set(db_session, betrieb, GRID, 15)
    _set(db_session, betrieb, SLOT, 45)

    def _slot(key):
        return store.get_slot(db_session, dealership_id=betrieb, location_id=None, key=key)

    def _joint(grid, slot):
        store.change_settings(
            db_session, dealership_id=betrieb, actor_id=None,
            writes=[
                store.SettingWrite(key=GRID, value=Decimal(grid), expected_version=_slot(GRID).version),
                store.SettingWrite(key=SLOT, value=Decimal(slot), expected_version=_slot(SLOT).version),
            ],
        )

    with pytest.raises(SettingValueError) as caught:
        _joint(10, 25)
    db_session.rollback()
    assert caught.value.reason == "setting_not_a_multiple_of_setting"
    assert (_slot(GRID).value, _slot(SLOT).value) == (Decimal(15), Decimal(45))
    _joint(10, 40)
    db_session.commit()
    assert (_slot(GRID).value, _slot(SLOT).value) == (Decimal(10), Decimal(40))


def _findings(db) -> list[uuid.UUID]:
    check = next(c for c in reconciliation.CHECKS if c.label == _CHECK)
    return [row for row, _ in find_rows_in_state(db, check)]


def _slot_id(db, dealership_id, key, location_id) -> uuid.UUID:
    return db.scalar(
        select(SettingValue.id).where(
            SettingValue.dealership_id == dealership_id,
            SettingValue.key == key,
            SettingValue.location_id == location_id,
        )
    )


def test_reconciliation_reports_a_scope_a_reactivated_location_left_broken(db_session, betrieb):
    north = samples.add_location(db_session, betrieb, name="Filiale Nord")
    south = samples.add_location(db_session, betrieb, name="Filiale Süd")
    _set(db_session, betrieb, GRID, 15)
    _set(db_session, betrieb, SLOT, 30)
    _set(db_session, betrieb, SLOT, 45, location_id=north)  # 45 of the Betrieb's 15
    _set(db_session, betrieb, GRID, 15, location_id=south)  # divides the Betrieb's 30
    assert _findings(db_session) == []

    for location_id in (north, south):
        db_session.get(Location, location_id).is_active = False
    db_session.commit()
    _set(db_session, betrieb, GRID, 10)  # the Betrieb: 30 of 10; north is not read while inactive
    _set(db_session, betrieb, SLOT, 20)  # the Betrieb: 20 of 10; south is not read while inactive
    assert _findings(db_session) == []
    for location_id in (north, south):
        db_session.get(Location, location_id).is_active = True
    db_session.commit()

    assert sorted(_findings(db_session), key=str) == sorted(
        [_slot_id(db_session, betrieb, SLOT, north), _slot_id(db_session, betrieb, GRID, south)], key=str
    )
