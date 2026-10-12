"""KAN-194 (FR-C-10): open mapping gaps recorded under a numeric CodeGrpNr are
re-keyed to their semantic code group by migration `ff5b97c11665`, merged into
an existing gap of the same key, and never dropped; and nothing in the code
can record a numeric code group again.

The migration's real `upgrade()` runs on Postgres over the create_all schema
(`tests/test_kan86_value_migrations.py`'s pattern).
"""

import datetime as dt
import importlib.util
import os
import pathlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import catalogue_admin, catalogue_sync
from app.vehicle.services.provider import resolve_provider_code
from tests.test_vehicle_catalogue_sync import _make_connection, _make_mock_provider

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "vehicle"
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load(pattern: str, name: str):
    (path,) = sorted(_VERSIONS.glob(pattern))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load("ff5b97c11665_*.py", "kan194_legacy_gap_code_groups")

_T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _gap(db: Session, *, code_group: str, provider_code: str = "7", vehicle_kind: str = "01", days: int = 0,
         occurrences: int = 1, resolved: bool = False, last_days: int | None = None) -> MappingGap:
    gap = MappingGap(
        provider="auto_i_dat", vehicle_kind=vehicle_kind, code_group=code_group, provider_code=provider_code,
        first_seen_at=_T0 + dt.timedelta(days=days),
        last_seen_at=_T0 + dt.timedelta(days=days if last_days is None else last_days),
        occurrences=occurrences, resolved=resolved,
        resolved_value_code="diesel" if resolved else None,
        resolved_at=_T0 if resolved else None,
    )
    db.add(gap)
    db.commit()
    return gap


def _upgrade(engine) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _MIGRATION.upgrade()


def _gaps(db: Session) -> list[MappingGap]:
    db.expire_all()
    return list(db.scalars(select(MappingGap).order_by(MappingGap.code_group, MappingGap.provider_code)))


def test_the_frozen_table_agrees_with_the_seed_migrations_groups():
    seed = _load("7c4e9a2b6d13_*.py", "kan194_seed_groups")
    assert _MIGRATION.LEGACY_CODE_GROUPS == {group_nr: code_group for group_nr, _k, code_group, _m in seed.GROUPS}


@_postgres_only
def test_a_numeric_gap_is_rekeyed_to_its_semantic_code_group(engine, db_session):
    gap = _gap(db_session, code_group="011", occurrences=4)

    _upgrade(engine)

    (after,) = _gaps(db_session)
    assert after.id == gap.id
    assert (after.code_group, after.provider_code, after.occurrences, after.resolved) == ("fuel_type", "7", 4, False)


@_postgres_only
def test_a_numeric_gap_merges_into_the_existing_semantic_gap_of_the_same_key(engine, db_session):
    semantic = _gap(db_session, code_group="fuel_type", days=5, occurrences=2, last_days=6)
    _gap(db_session, code_group="011", days=1, occurrences=3, last_days=9)
    # A second legacy group for the same vocabulary and kind folds in too.
    _gap(db_session, code_group="021", days=3, occurrences=1, last_days=4)

    _upgrade(engine)

    (after,) = _gaps(db_session)
    assert after.id == semantic.id
    assert after.code_group == "fuel_type"
    assert after.occurrences == 2 + 3 + 1
    assert after.first_seen_at == _T0 + dt.timedelta(days=1)
    assert after.last_seen_at == _T0 + dt.timedelta(days=9)


@_postgres_only
def test_merging_into_a_resolved_semantic_gap_keeps_its_resolution(engine, db_session):
    resolved = _gap(db_session, code_group="fuel_type", resolved=True)
    _gap(db_session, code_group="011", occurrences=5)

    _upgrade(engine)

    (after,) = _gaps(db_session)
    assert after.id == resolved.id
    assert (after.resolved, after.resolved_value_code, after.occurrences) == (True, "diesel", 6)


@_postgres_only
def test_two_legacy_gaps_of_one_key_become_one_kept_from_the_first_seen(engine, db_session):
    first = _gap(db_session, code_group="013", provider_code="M", days=2, occurrences=1)
    _gap(db_session, code_group="023", provider_code="M", days=8, occurrences=2)

    _upgrade(engine)

    (after,) = _gaps(db_session)
    assert after.id == first.id
    assert (after.code_group, after.occurrences) == ("transmission", 3)


@_postgres_only
def test_no_code_is_dropped_and_other_keys_stay_apart(engine, db_session):
    _gap(db_session, code_group="011", provider_code="7")
    _gap(db_session, code_group="011", provider_code="8")
    _gap(db_session, code_group="011", provider_code="7", vehicle_kind="02")
    _gap(db_session, code_group="112", provider_code="2", vehicle_kind="03")

    _upgrade(engine)

    assert {(g.vehicle_kind, g.code_group, g.provider_code) for g in _gaps(db_session)} == {
        ("01", "fuel_type", "7"), ("01", "fuel_type", "8"), ("02", "fuel_type", "7"),
        ("03", "engine_cycle", "2"),  # a stroke count, never a drivetrain (R-C-5)
    }


@_postgres_only
def test_an_unmapped_numeric_gap_is_left_untouched_and_reported(engine, db_session, capsys):
    gap = _gap(db_session, code_group="014", occurrences=3)

    _upgrade(engine)

    (after,) = _gaps(db_session)
    assert (after.id, after.code_group, after.occurrences) == (gap.id, "014", 3)
    assert f"NOT rewritten gap {gap.id}: code group '014'" in capsys.readouterr().out


@_postgres_only
def test_resolved_and_semantic_gaps_are_not_touched_and_a_rerun_is_a_no_op(engine, db_session, capsys):
    resolved_legacy = _gap(db_session, code_group="011", resolved=True)
    semantic = _gap(db_session, code_group="body_style", provider_code="2")

    _upgrade(engine)
    first = {(g.id, g.code_group, g.occurrences, g.updated_at) for g in _gaps(db_session)}
    _upgrade(engine)

    assert {(g.id, g.code_group) for g in _gaps(db_session)} == {
        (resolved_legacy.id, "011"), (semantic.id, "body_style")
    }
    assert {(g.id, g.code_group, g.occurrences, g.updated_at) for g in _gaps(db_session)} == first
    assert "0 legacy mapping gaps re-keyed to their semantic code group, 0 merged" in capsys.readouterr().out


@_postgres_only
def test_a_rekeyed_gap_can_now_be_resolved(engine, db_session):
    """The point of the ticket: the 409 (KAN-77) no longer applies, because
    the gap now names a list."""

    ref_list = ReferenceList(list_code="fuel_type")
    db_session.add(ref_list)
    db_session.flush()
    db_session.add(
        ReferenceValue(list_id=ref_list.id, value_code="diesel", label_de="d", label_fr="d", label_it="d", label_en="d")
    )
    db_session.commit()
    _gap(db_session, code_group="011")

    _upgrade(engine)
    (gap,) = _gaps(db_session)
    catalogue_admin.resolve_gap(
        db_session, gap=gap, canonical_list_code="fuel_type", canonical_value_code="diesel", actor_id=uuid.uuid4(),
    )

    assert gap.resolved is True
    assert db_session.scalar(select(ProviderCodeMap.code_group)) == "fuel_type"


@_postgres_only
def test_the_downgrade_is_a_no_op(engine, db_session):
    _gap(db_session, code_group="011")
    _upgrade(engine)

    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _MIGRATION.downgrade()

    assert [g.code_group for g in _gaps(db_session)] == ["fuel_type"]


# --- (b) nothing can record a numeric code group again -------------------------------------------


def test_recording_a_gap_under_a_numeric_code_group_is_refused(db_session):
    with pytest.raises(ValueError, match="semantic code group"):
        resolve_provider_code(db_session, provider="auto_i_dat", vehicle_kind="01", code_group="011", provider_code="3")

    assert db_session.scalar(select(MappingGap)) is None


def test_every_gap_the_catalogue_sync_records_carries_a_semantic_code_group(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    groups = {gap.code_group for gap in db_session.scalars(select(MappingGap))}
    assert groups, "the mock sync with an empty code map must record gaps"
    assert not any(group.isdigit() for group in groups)
    assert groups <= {*catalogue_sync._CODED_FIELDS, "equipment_feature"}
