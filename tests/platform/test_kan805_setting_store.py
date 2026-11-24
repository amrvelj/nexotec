"""KAN-805 (ADR-214): the settings store — declarations, the read through
platform.public, «not set» as a state, values per Betrieb and per location,
history, restore as a change, concurrency, the export."""

import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError, NotFoundError
from app.platform import public as platform_public
from app.platform.organisation.models.setting_value import (
    SettingChangeOrigin,
    SettingState,
    SettingValue,
    SettingValueChange,
)
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.setting_declarations import (
    SettingDeclaration,
    SettingDeclarationError,
    SettingKind,
    SettingValueError,
    TextValue,
    WhenNotSet,
    declared_settings,
    register_setting,
)
from tests import setting_store_support as s
from tests.identity_support import create_dealership
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION


@pytest.fixture()
def samples():
    with s.declared(*s.SAMPLES):
        yield


@pytest.fixture()
def betrieb(client):
    return uuid.UUID(create_dealership(client))


def _change(db, dealership_id, key, value, *, location_id=None, version=None, actor=None):
    if version is None:
        version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    slot = store.change_setting(
        db,
        dealership_id=dealership_id,
        location_id=location_id,
        key=key,
        value=value,
        expected_version=version,
        actor_id=actor,
    )
    db.commit()
    return slot


def _read(db, dealership_id, key, location_id=None):
    return platform_public.read_setting(db, dealership_id=dealership_id, location_id=location_id, key=key)


# --- declarations ------------------------------------------------------------------


def test_nothing_is_declared_in_the_shipped_app():
    assert [d.key for d in declared_settings() if not d.key.startswith("testarea.")] == []


@pytest.mark.parametrize(
    "declaration",
    [
        SettingDeclaration(key="NoDot", module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET),
        SettingDeclaration(key="a." + "x" * 70, module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET),
        SettingDeclaration(key="a.choice", module=None, kind=SettingKind.CHOICE, when_not_set=WhenNotSet.QUIET),
        SettingDeclaration(
            key="a.fee", module=None, kind=SettingKind.AMOUNT, when_not_set=WhenNotSet.ACTS_AS, acts_as=Decimal("5.00")
        ),
        SettingDeclaration(
            key="a.text", module=None, kind=SettingKind.TEXT, when_not_set=WhenNotSet.ACTS_AS, acts_as="Hallo"
        ),
        SettingDeclaration(
            key="a.unit", module=None, kind=SettingKind.CHOICE, when_not_set=WhenNotSet.ACTS_AS, acts_as="days",
            choices=("hours",),
        ),
        SettingDeclaration(key="a.quiet", module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET, acts_as=True),
        SettingDeclaration(
            key="a.bounds", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET,
            minimum=Decimal(5), maximum=Decimal(1),
        ),
        SettingDeclaration(
            key="a.blocks", module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET,
            checklist_blocks="issue_invoice",
        ),
    ],
)
def test_a_wrong_declaration_is_refused_at_start_up(declaration):
    with pytest.raises(SettingDeclarationError):
        register_setting(declaration)
    assert declaration.key not in {d.key for d in declared_settings()}


def test_the_same_declaration_again_is_harmless_another_under_its_key_is_refused(samples):
    register_setting(s.WAIT)
    other = SettingDeclaration(key=s.WAIT.key, module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET)
    with pytest.raises(SettingDeclarationError):
        register_setting(other)


def test_an_undeclared_key_is_a_programming_error(db_session, betrieb):
    with pytest.raises(store.UndeclaredSettingError):
        _read(db_session, betrieb, "testarea.nobody_declared_this")


# --- read: not set, Betrieb, location ----------------------------------------------


def test_not_set_is_a_state_and_says_what_it_does(db_session, betrieb, samples):
    quiet = _read(db_session, betrieb, s.WAIT.key)
    unit = _read(db_session, betrieb, s.UNIT.key)
    refuses = _read(db_session, betrieb, s.SWITCH.key)

    assert (quiet.is_set, quiet.value, quiet.source, quiet.when_not_set, quiet.effective) == (
        False, None, None, WhenNotSet.QUIET, None,
    )
    assert (unit.is_set, unit.acts_as, unit.effective) == (False, "hours", "hours")
    assert (refuses.is_set, refuses.when_not_set) == (False, WhenNotSet.REFUSES)
    assert db_session.scalar(select(func.count()).select_from(SettingValue)) == 0


def test_the_location_value_wins_else_the_betriebs_else_not_set(db_session, betrieb, samples):
    nord = s.add_location(db_session, betrieb, name="Nord")
    sued = s.add_location(db_session, betrieb, name="Süd")

    _change(db_session, betrieb, s.WAIT.key, Decimal(15))
    _change(db_session, betrieb, s.WAIT.key, Decimal(30), location_id=nord)

    at_nord, at_sued, own = (_read(db_session, betrieb, s.WAIT.key, loc) for loc in (nord, sued, None))
    assert (at_nord.value, at_nord.source) == (Decimal(30), "location")
    assert (at_sued.value, at_sued.source) == (Decimal(15), "dealership")
    assert (own.value, own.source) == (Decimal(15), "dealership")

    # The Betrieb's value cleared: Nord keeps its own, Süd has none.
    _change(db_session, betrieb, s.WAIT.key, store.NOT_SET)
    assert _read(db_session, betrieb, s.WAIT.key, nord).value == Decimal(30)
    assert _read(db_session, betrieb, s.WAIT.key, sued).is_set is False

    # Nord's own value cleared: it takes the Betrieb's again.
    _change(db_session, betrieb, s.WAIT.key, Decimal(20))
    _change(db_session, betrieb, s.WAIT.key, store.NOT_SET, location_id=nord)
    assert (_read(db_session, betrieb, s.WAIT.key, nord).value, _read(db_session, betrieb, s.WAIT.key, nord).source) == (
        Decimal(20), "dealership",
    )


def test_another_betriebs_value_is_never_read(client, db_session, betrieb, samples):
    other = uuid.UUID(create_dealership(client, legalName="Garage Nachbar AG"))
    _change(db_session, other, s.WAIT.key, Decimal(45))

    assert _read(db_session, betrieb, s.WAIT.key).is_set is False
    other_location = s.add_location(db_session, other)
    with pytest.raises(NotFoundError):
        store.get_slot(db_session, dealership_id=betrieb, location_id=other_location, key=s.WAIT.key)
    with pytest.raises(NotFoundError):
        _change(db_session, betrieb, s.WAIT.key, Decimal(5), location_id=other_location, version=0)


def test_each_kind_round_trips(db_session, betrieb, samples):
    _change(db_session, betrieb, s.SWITCH.key, True)
    _change(db_session, betrieb, s.UNIT.key, "work_units")
    _change(db_session, betrieb, s.FEE.key, Decimal("4.5"))
    _change(db_session, betrieb, s.NOTE.key, TextValue(name=" Schlüssel am Empfang ", translations=(("fr", "Clés à la réception"),)))
    db_session.expire_all()

    assert _read(db_session, betrieb, s.SWITCH.key).value is True
    assert _read(db_session, betrieb, s.UNIT.key).value == "work_units"
    assert str(_read(db_session, betrieb, s.FEE.key).value) == "4.50"
    note = _read(db_session, betrieb, s.NOTE.key).value
    assert isinstance(note, TextValue)
    assert (note.name, note.in_language("fr"), note.in_language("it")) == (
        "Schlüssel am Empfang", "Clés à la réception", "Schlüssel am Empfang",
    )


@pytest.mark.parametrize(
    ("declaration", "value", "reason"),
    [
        (s.SWITCH, "yes", "setting_value_not_a_switch"),
        (s.WAIT, Decimal("1.5"), "setting_value_too_many_decimals"),
        (s.WAIT, Decimal(241), "setting_value_above_maximum"),
        (s.WAIT, Decimal(-1), "setting_value_below_minimum"),
        (s.WAIT, 1.0, "setting_value_not_a_number"),
        (s.FEE, Decimal("1.005"), "setting_value_too_many_decimals"),
        (s.UNIT, "days", "setting_value_not_a_choice"),
        (s.NOTE, TextValue(name="  "), "setting_value_text_length"),
        (s.NOTE, TextValue(name="x", translations=(("es", "y"),)), "setting_value_language"),
    ],
)
def test_a_value_that_does_not_fit_is_refused_with_its_reason(db_session, betrieb, samples, declaration, value, reason):
    with pytest.raises(SettingValueError) as refused:
        _change(db_session, betrieb, declaration.key, value, version=0)
    assert refused.value.reason == reason


def test_an_inactive_locations_own_value_is_not_changed(db_session, betrieb, samples):
    closed = s.add_location(db_session, betrieb, active=False)
    with pytest.raises(store.SettingRefused) as refused:
        _change(db_session, betrieb, s.WAIT.key, Decimal(5), location_id=closed, version=0)
    assert refused.value.reason == "setting_location_inactive"


# --- history, audit, restore, concurrency ------------------------------------------


def test_every_change_is_kept_with_who_and_when_and_audited(db_session, betrieb, samples):
    actor = uuid.uuid4()
    _change(db_session, betrieb, s.WAIT.key, Decimal(10), actor=actor)
    _change(db_session, betrieb, s.WAIT.key, Decimal(20), actor=actor)
    _change(db_session, betrieb, s.WAIT.key, store.NOT_SET, actor=actor)

    history = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key)
    assert [(c.is_set, c.value, c.version_after, c.origin, c.created_by) for c in history] == [
        (False, None, 3, SettingChangeOrigin.EDIT, actor),
        (True, Decimal(20), 2, SettingChangeOrigin.EDIT, actor),
        (True, Decimal(10), 1, SettingChangeOrigin.EDIT, actor),
    ]
    audits = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "setting_value", AuditEvent.tenant_id == betrieb)
    ).all()
    assert [(a.before["value"], a.after["value"]) for a in sorted(audits, key=lambda a: a.created_at)] == [
        (None, "10"), ("10", "20"), ("20", None),
    ]


def test_a_write_that_changes_nothing_writes_nothing(db_session, betrieb, samples):
    _change(db_session, betrieb, s.WAIT.key, Decimal(10))
    slot = _change(db_session, betrieb, s.WAIT.key, Decimal("10.0"))
    assert slot.version == 1
    assert db_session.scalar(select(func.count()).select_from(SettingValueChange)) == 1


def test_a_restore_brings_an_earlier_value_back_as_a_change_of_its_own(db_session, betrieb, samples):
    _change(db_session, betrieb, s.WAIT.key, Decimal(10))
    _change(db_session, betrieb, s.WAIT.key, Decimal(20))
    first = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key)[-1]

    slot = store.restore_change(
        db_session, dealership_id=betrieb, change_id=first.id, expected_version=2, actor_id=None
    )
    db_session.commit()

    assert (slot.value, slot.version) == (Decimal(10), 3)
    newest = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key)[0]
    assert (newest.origin, newest.restored_from_id, newest.value) == (SettingChangeOrigin.RESTORE, first.id, Decimal(10))
    assert len(store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key)) == 3


def test_a_restore_of_not_set_clears_the_slot(db_session, betrieb, samples):
    _change(db_session, betrieb, s.WAIT.key, Decimal(10))
    _change(db_session, betrieb, s.WAIT.key, store.NOT_SET)
    _change(db_session, betrieb, s.WAIT.key, Decimal(30))
    cleared = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key)[1]

    slot = store.restore_change(db_session, dealership_id=betrieb, change_id=cleared.id, expected_version=3, actor_id=None)
    db_session.commit()
    assert slot.is_set is False
    row = db_session.scalar(select(SettingValue).where(SettingValue.dealership_id == betrieb))
    assert row is not None and row.state == SettingState.NOT_SET


def test_a_value_that_no_longer_fits_is_not_restored(db_session, betrieb):
    loose = SettingDeclaration(key="testarea.limit", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET)
    with s.declared(loose):
        _change(db_session, betrieb, loose.key, Decimal(500))
        _change(db_session, betrieb, loose.key, Decimal(50))
        old = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=loose.key)[-1]
    tight = SettingDeclaration(
        key="testarea.limit", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET, maximum=Decimal(100)
    )
    with s.declared(tight), pytest.raises(SettingValueError) as refused:
        store.restore_change(db_session, dealership_id=betrieb, change_id=old.id, expected_version=2, actor_id=None)
    assert refused.value.reason == "setting_value_no_longer_valid"


def test_another_betriebs_change_is_not_found(client, db_session, betrieb, samples):
    other = uuid.UUID(create_dealership(client, legalName="Garage Nachbar AG"))
    _change(db_session, other, s.WAIT.key, Decimal(45))
    theirs = store.setting_history(db_session, dealership_id=other, location_id=None, key=s.WAIT.key)[0]
    with pytest.raises(NotFoundError):
        store.restore_change(db_session, dealership_id=betrieb, change_id=theirs.id, expected_version=0, actor_id=None)


def test_a_stale_version_is_a_conflict_the_first_write_included(db_session, betrieb, samples):
    _change(db_session, betrieb, s.WAIT.key, Decimal(10), version=0)
    with pytest.raises(ConflictError):
        _change(db_session, betrieb, s.WAIT.key, Decimal(20), version=0)
    db_session.rollback()
    with pytest.raises(ConflictError):
        _change(db_session, betrieb, s.WAIT.key, Decimal(20), version=5)


# --- review fixes (KAN-805 PR-1) ----------------------------------------------------


def test_clearing_a_slot_never_written_writes_nothing(db_session, betrieb, samples):
    slot = _change(db_session, betrieb, s.WAIT.key, store.NOT_SET, version=0)

    assert (slot.is_set, slot.version) == (False, 0)
    assert db_session.scalar(select(func.count()).select_from(SettingValue)) == 0
    assert db_session.scalar(select(func.count()).select_from(SettingValueChange)) == 0
    assert db_session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "setting_value")) == 0


@pytest.mark.parametrize("declaration", [s.WAIT, s.FEE])
def test_a_number_the_column_cannot_hold_is_refused_by_the_declaration(db_session, betrieb, declaration):
    unbounded = SettingDeclaration(
        key=declaration.key, module=None, kind=declaration.kind, when_not_set=WhenNotSet.QUIET
    )
    with s.declared(unbounded), pytest.raises(SettingValueError) as refused:
        _change(db_session, betrieb, unbounded.key, Decimal("1E14"), version=0)
    assert refused.value.reason == "setting_value_too_large"


def test_the_version_checked_is_the_committed_one_not_a_row_the_session_holds(engine, db_session, betrieb, samples):
    from sqlalchemy.orm import sessionmaker

    _change(db_session, betrieb, s.WAIT.key, Decimal(10))
    held = db_session.scalar(select(SettingValue).where(SettingValue.dealership_id == betrieb))
    assert held is not None and held.version == 1  # the session holds the row at version 1
    other = sessionmaker(bind=engine)()
    try:
        _change(other, betrieb, s.WAIT.key, Decimal(20), version=1)
    finally:
        other.close()

    with pytest.raises(ConflictError):
        store.change_setting(
            db_session, dealership_id=betrieb, location_id=None, key=s.WAIT.key, value=Decimal(30),
            expected_version=1, actor_id=None,
        )
    assert held.version == 2  # read again under the lock


@NEEDS_ITS_OWN_CONNECTION
def test_two_first_writes_at_once_are_one_change_and_one_conflict(engine, betrieb, samples):
    """The advisory lock serialises the Betrieb's writes: the second first
    write waits, then sees version 1 and is a conflict — never a duplicate
    slot or an IntegrityError."""

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def write(value: Decimal) -> None:
        session = factory()
        try:
            start.wait()
            store.change_setting(
                session, dealership_id=betrieb, location_id=None, key=s.WAIT.key, value=value,
                expected_version=0, actor_id=None,
            )
            session.commit()
            outcome = "written"
        except ConflictError:
            session.rollback()
            outcome = "conflict"
        except BaseException as exc:  # noqa: BLE001 - reported below
            session.rollback()
            outcome = type(exc).__name__
        finally:
            session.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=write, args=(Decimal(v),)) for v in (10, 20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(outcomes) == ["conflict", "written"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SettingValue)) == 1
        assert session.scalar(select(func.count()).select_from(SettingValueChange)) == 1


def test_a_text_setting_is_cleared_back_to_not_set(db_session, betrieb, samples):
    _change(db_session, betrieb, s.NOTE.key, TextValue(name="Schlüssel", translations=(("fr", "Clés"),)))
    _change(db_session, betrieb, s.NOTE.key, store.NOT_SET)

    row = db_session.scalar(select(SettingValue).where(SettingValue.key == s.NOTE.key))
    assert row is not None and (row.state, row.value_text, row.value_text_translations) == (SettingState.NOT_SET, None, None)
    assert _read(db_session, betrieb, s.NOTE.key).is_set is False


def test_a_value_of_a_kind_the_setting_no_longer_has_is_not_restored(db_session, betrieb):
    as_number = SettingDeclaration(key="testarea.shape", module=None, kind=SettingKind.NUMBER, when_not_set=WhenNotSet.QUIET)
    with s.declared(as_number):
        _change(db_session, betrieb, as_number.key, Decimal(3))
        old = store.setting_history(db_session, dealership_id=betrieb, location_id=None, key=as_number.key)[0]
    as_switch = SettingDeclaration(key="testarea.shape", module=None, kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET)
    with s.declared(as_switch), pytest.raises(SettingValueError) as refused:
        store.restore_change(db_session, dealership_id=betrieb, change_id=old.id, expected_version=1, actor_id=None)
    assert refused.value.reason == "setting_value_no_longer_valid"


def test_the_tests_area_and_an_unknown_module_are_refused_outside_the_tests():
    with pytest.raises(SettingDeclarationError):
        register_setting(s.WAIT)
    unknown = SettingDeclaration(key="werkstatt.x", module="werkstatt", kind=SettingKind.SWITCH, when_not_set=WhenNotSet.QUIET)
    with pytest.raises(SettingDeclarationError):
        register_setting(unknown)


def test_the_betriebs_settings_rows_reach_its_export_and_no_other(client, db_session, betrieb, samples):
    from app.core.export import ExportScope
    from app.platform.public import PlatformExporter

    other = uuid.UUID(create_dealership(client, legalName="Garage Nachbar AG"))
    _change(db_session, betrieb, s.WAIT.key, Decimal(10))
    _change(db_session, other, s.WAIT.key, Decimal(99))
    tables = {t.table.name: t for t in PlatformExporter().exported_tables()}

    for name in ("setting_value", "setting_value_change"):
        exported = tables[name]
        scope = ExportScope(dealership_id=betrieb, group_id=uuid.uuid4())
        rows = db_session.execute(select(exported.table).where(exported.scope(scope))).all()
        assert [row.dealership_id for row in rows] == [betrieb], name
