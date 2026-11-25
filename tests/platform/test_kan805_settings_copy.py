"""KAN-805 PR-4 (ADR-214; KAN-220's copy, FR-A-01): «copy configuration from
a sister Betrieb» copies the source's own setting values — through the
store's own write path, as changes of origin `copy` — for the settings the
target configures. «Not set» in the source keeps the target's value; a
location's value is never copied; a source value that no longer fits its
declaration is skipped."""

import dataclasses
import hashlib
import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.permissions import ModuleCode
from app.platform.organisation.models.setting_value import SettingChangeOrigin, SettingValueChange
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.setting_declarations import TextValue
from tests import setting_store_support as samples
from tests.platform.test_configuration_copy import _copy, _entitle, _pair
from tests.postgres_only import ROW_LOCKS

SETTINGS = ("settings",)


@pytest.fixture()
def declared():
    with samples.declared(*samples.SAMPLES):
        yield


@contextmanager
def _session(engine):
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _set(engine, dealership, key, value, location_id=None):
    with _session(engine) as db:
        dealership_id = uuid.UUID(dealership)
        version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
        store.change_setting(
            db, dealership_id=dealership_id, location_id=location_id, key=key, value=value,
            expected_version=version, actor_id=None,
        )
        db.commit()


def _value(engine, dealership, key, location_id=None):
    with _session(engine) as db:
        slot = store.get_slot(db, dealership_id=uuid.UUID(dealership), location_id=location_id, key=key)
        return slot.value if slot.is_set else None


def _items(response):
    assert response.status_code == 200, response.text
    return {item["key"]: (item["outcome"], item["reason"]) for item in response.json()["items"]}


def _aftersales_pair(client, engine):
    source, target, user, in_target, _ = _pair(client, engine)
    _entitle(engine, source, target, modules=(ModuleCode.AFTERSALES,))
    return source, target, user, in_target


def test_the_dry_run_lists_what_the_commit_then_writes(client, engine, declared):
    source, target, _, headers = _aftersales_pair(client, engine)
    _set(engine, source, samples.WAIT.key, Decimal(15))  # target never set: created
    _set(engine, source, samples.SWITCH.key, True)
    _set(engine, target, samples.SWITCH.key, False)  # overwritten
    _set(engine, source, samples.UNIT.key, "hours")
    _set(engine, target, samples.UNIT.key, "hours")  # unchanged
    _set(engine, source, samples.NOTE.key, TextValue(name="Annahme", translations=(("fr", "Réception"),)))

    expected = {
        samples.WAIT.key: ("created", None),
        samples.SWITCH.key: ("overwritten", None),
        samples.UNIT.key: ("unchanged", None),
        samples.NOTE.key: ("created", None),
    }
    preview = _copy(client, target, headers, source, dry_run=True, subsets=SETTINGS)
    assert _items(preview) == expected
    assert _value(engine, target, samples.WAIT.key) is None  # the dry run wrote nothing

    done = _copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)
    assert _items(done) == expected
    assert _value(engine, target, samples.WAIT.key) == Decimal(15)
    assert _value(engine, target, samples.SWITCH.key) is True
    assert _value(engine, target, samples.NOTE.key) == TextValue(name="Annahme", translations=(("fr", "Réception"),))


def test_a_copied_value_is_a_change_of_origin_copy_by_the_manager(client, engine, declared):
    source, target, user, headers = _aftersales_pair(client, engine)
    _set(engine, source, samples.FEE.key, Decimal("4.50"))
    assert _copy(client, target, headers, source, dry_run=False, subsets=SETTINGS).status_code == 200
    with _session(engine) as db:
        change = db.scalars(
            select(SettingValueChange).where(
                SettingValueChange.dealership_id == uuid.UUID(target), SettingValueChange.key == samples.FEE.key
            )
        ).one()
        assert (change.origin, change.created_by) == (SettingChangeOrigin.COPY, uuid.UUID(user["id"]))


def test_not_set_in_the_source_keeps_the_targets_value(client, engine, declared):
    source, target, _, headers = _aftersales_pair(client, engine)
    _set(engine, target, samples.WAIT.key, Decimal(30))
    _set(engine, source, samples.WAIT.key, Decimal(15))
    _set(engine, source, samples.WAIT.key, store.NOT_SET)  # written, then cleared
    assert _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)) == {}
    assert _value(engine, target, samples.WAIT.key) == Decimal(30)


def test_a_locations_value_is_never_copied(client, engine, declared):
    source, target, _, headers = _aftersales_pair(client, engine)
    with _session(engine) as db:
        nord = samples.add_location(db, uuid.UUID(source), name="Nord")
    _set(engine, source, samples.WAIT.key, Decimal(20), location_id=nord)
    assert _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS)) == {}
    with _session(engine) as db:
        assert store.list_slots(db, dealership_id=uuid.UUID(target), keys=[samples.WAIT.key]) == []


def test_only_settings_of_modules_the_target_holds_are_copied(client, engine, declared):
    source, target, _, in_target, _ = _pair(client, engine)
    _entitle(engine, source, modules=(ModuleCode.AFTERSALES,))  # the target holds sales only
    _set(engine, source, samples.WAIT.key, Decimal(15))
    _set(engine, source, samples.NOTE.key, TextValue(name="Annahme"))  # no module: every Betrieb
    items = _items(_copy(client, target, in_target, source, dry_run=False, subsets=SETTINGS))
    assert items == {samples.NOTE.key: ("created", None)}


def test_a_source_value_that_no_longer_fits_is_skipped(client, engine):
    source, target, _, headers = _aftersales_pair(client, engine)
    with samples.declared(samples.WAIT):
        _set(engine, source, samples.WAIT.key, Decimal(200))
    tight = dataclasses.replace(samples.WAIT, maximum=Decimal(120))
    with samples.declared(tight):
        items = _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS))
        assert items == {samples.WAIT.key: ("skipped", "setting_value_stale")}
        assert _value(engine, target, samples.WAIT.key) is None


def test_a_copied_value_can_be_restored_from_the_targets_history(client, engine, declared):
    source, target, _, headers = _aftersales_pair(client, engine)
    _set(engine, target, samples.WAIT.key, Decimal(30))
    _set(engine, source, samples.WAIT.key, Decimal(15))
    assert _copy(client, target, headers, source, dry_run=False, subsets=SETTINGS).status_code == 200
    with _session(engine) as db:
        history = store.setting_history(db, dealership_id=uuid.UUID(target), location_id=None, key=samples.WAIT.key)
        before_copy = next(change for change in history if change.origin == SettingChangeOrigin.EDIT)
        store.restore_change(
            db, dealership_id=uuid.UUID(target), change_id=before_copy.id, expected_version=2, actor_id=None
        )
        db.commit()
    assert _value(engine, target, samples.WAIT.key) == Decimal(30)


def test_a_stale_target_value_is_reported_as_overwritten(client, engine):
    source, target, _, headers = _aftersales_pair(client, engine)
    with samples.declared(samples.WAIT):
        _set(engine, target, samples.WAIT.key, Decimal(200))
        _set(engine, source, samples.WAIT.key, Decimal(100))
    tight = dataclasses.replace(samples.WAIT, maximum=Decimal(120))
    with samples.declared(tight):
        items = _items(_copy(client, target, headers, source, dry_run=False, subsets=SETTINGS))
        assert items == {samples.WAIT.key: ("overwritten", None)}
        assert _value(engine, target, samples.WAIT.key) == Decimal(100)


@ROW_LOCKS
def test_the_targets_settings_are_locked_from_the_first_read(client, engine, declared, monkeypatch):
    """A colleague's change waits for the copy and then meets the moved
    version, as for the other subsets — it never slips in between the copy's
    read and its write, which would fail the whole copy with 409. Probed from
    a second session when the copy reads the target's first slot."""

    source, target, _, headers = _aftersales_pair(client, engine)
    _set(engine, source, samples.WAIT.key, Decimal(15))
    probes: list[bool] = []
    original = store.get_slot

    def probing_get_slot(db, *, dealership_id, location_id, key):
        if dealership_id == uuid.UUID(target) and not probes:
            colleague = sessionmaker(bind=engine)()
            try:
                digest = hashlib.sha256(f"platform.setting_store:{dealership_id}".encode()).digest()
                got = colleague.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": int.from_bytes(digest[:8], "big", signed=True)},
                ).scalar_one()
                probes.append(not got)
            finally:
                colleague.rollback()
                colleague.close()
        return original(db, dealership_id=dealership_id, location_id=location_id, key=key)

    monkeypatch.setattr(store, "get_slot", probing_get_slot)
    assert _copy(client, target, headers, source, dry_run=False, subsets=SETTINGS).status_code == 200
    assert probes == [True]
