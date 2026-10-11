"""KAN-175 (KAN-86 step 3): the eight per-context migrations that sweep the
member names step-1 instances wrote during step 2's deploy, refuse a stored
string that is neither form, and give every enum column its CHECK constraint
(alembic/versions/<context>/*_kan175_<context>_enum_values_only.py) — plus the
outbox index and the integration connection CHECK narrowed to the values.

Each migration freezes its own column list; the first tests check it still
agrees with the models. (That a migrated database carries exactly the CHECKs
the models declare is tests/test_migrated_enum_checks.py, KAN-296.) The rest
run the real upgrade()/downgrade() on Postgres over a schema without the
CHECKs (a database from before KAN-175, tests/enum_test_support.py), walk the
Vehicles list sorted by status over the swept rows (KAN-183, folded into this
ticket by Anto on 2026-10-11), and pin that the constraint helpers read the
current schema only (KAN-298).
"""

import os
import pathlib
import uuid
from collections import defaultdict

import pytest
import sqlalchemy as sa
from sqlalchemy import text

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.enum_type import StoredEnum, enum_check_name
from app.db import Base
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleStatus
from tests.enum_test_support import (
    drop_enum_checks,
    enum_checks,
    scope_check_definition,
    widen_connection_scope_check,
)
from tests.test_kan86_value_migrations import _load, _raw, _run, _seed_one_row_each, _set_raw
from tests.test_vehicle_mdm_search_sort import _walk

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
_CONTEXTS = ("core", "platform", "customer", "vehicle", "sales", "inventory", "valuation", "integration")
_MIGRATIONS = {
    path.parent.name: _load(path) for path in sorted(_VERSIONS.glob("*/*_kan175_*_enum_values_only.py"))
}


def _model_columns() -> dict[str, dict[tuple[str, str], tuple[tuple[str, str], ...]]]:
    by_context: dict[str, dict[tuple[str, str], tuple[tuple[str, str], ...]]] = defaultdict(dict)
    for mapper in Base.registry.mappers:
        context = mapper.class_.__module__.split(".")[1]
        for column in mapper.local_table.columns:
            if isinstance(column.type, StoredEnum):
                by_context[context][(mapper.local_table.name, column.name)] = tuple(
                    (m.name, m.value) for m in column.type.enum_class
                )
    return by_context


def _frozen() -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    return {(t, c): pairs for module in _MIGRATIONS.values() for t, c, pairs in module._COLUMNS}


def _enum_checks(conn) -> set[str]:
    return {name for _table, name in enum_checks(conn)}


# --- the frozen lists match the models -------------------------------------------


def test_one_migration_per_context_chain():
    assert sorted(_MIGRATIONS) == sorted(_CONTEXTS)
    for context, module in _MIGRATIONS.items():
        parents = {p.stem.split("_", 1)[0] for p in (_VERSIONS / context).glob("*.py")}
        assert module.down_revision in parents, f"{context}: {module.down_revision} is not in its own chain"


def test_every_frozen_column_agrees_with_its_context_and_its_members():
    """Exact equality held when the lists were written (62 columns). A column
    or member added later is not in them: its own migration creates or
    replaces the CHECK (app/core/enum_type.py)."""

    models = _model_columns()
    for context, module in _MIGRATIONS.items():
        for table, column, pairs in module._COLUMNS:
            current = models[context].get((table, column))
            if current is None:
                continue  # removed since; its own migration owns that
            assert set(pairs) <= set(current), (context, table, column)
    assert len(_frozen()) == 62


def test_the_database_refuses_a_name_and_an_unknown_string(db_session):
    db_session.execute(
        text(
            "INSERT INTO vehicle_mdm (id, vehicle_number, vin, vehicle_status, catalogue_match_status, version, "
            "created_at, updated_at) VALUES (:id, 'F-X', 'VINREFUSEDXXXXXX1', 'active', 'unverified', 1, now(), now())"
        ),
        {"id": str(uuid.uuid4())},
    )
    db_session.commit()
    for refused in ("ACTIVE", "parked"):
        with pytest.raises(sa.exc.IntegrityError, match="ck_vehicle_mdm_vehicle_status_enum"):
            db_session.execute(text("UPDATE vehicle_mdm SET vehicle_status = :s"), {"s": refused})
        db_session.rollback()


# --- the real upgrade() / downgrade(), as alembic runs them -----------------------


@pytest.fixture()
def seeded_in_name_form(engine):
    """One row in every table with an enum column, every enum column in the
    member-NAME form, on a schema without KAN-175's constraints and with the
    connection CHECK as step 2 left it."""

    frozen = _frozen()
    tables = [Base.metadata.tables[name] for name in sorted({table for table, _ in frozen})]
    with engine.begin() as conn:
        drop_enum_checks(conn)
        widen_connection_scope_check(conn)
        _rebuild_outbox_index(conn, "status IN ('PENDING', 'pending')")
        _seed_one_row_each(conn, tables)
        for (table, column), pairs in frozen.items():
            _set_raw(conn, table, column, pairs[-1][0])
    return frozen


def _rebuild_outbox_index(conn, predicate: str) -> None:
    conn.execute(text("DROP INDEX ix_outbox_message_pending_next_attempt_at"))
    conn.execute(
        text(f"CREATE INDEX ix_outbox_message_pending_next_attempt_at ON outbox_message (next_attempt_at) WHERE {predicate}")
    )


def _index_definition(conn) -> str:
    return conn.execute(text("SELECT pg_get_indexdef('ix_outbox_message_pending_next_attempt_at'::regclass)")).scalar_one()


def test_upgrade_sweeps_every_name_then_constrains_every_column_and_downgrade_drops_them(engine, seeded_in_name_form):
    frozen = seeded_in_name_form
    with engine.begin() as conn:
        assert _enum_checks(conn) == set()
        for module in _MIGRATIONS.values():
            _run(conn, module.upgrade)

        assert {key: _raw(conn, *key) for key in frozen} == {key: pairs[-1][1] for key, pairs in frozen.items()}
        assert _enum_checks(conn) == {enum_check_name(table, column) for table, column in frozen}
        assert "= 'pending'" in _index_definition(conn) and "PENDING" not in _index_definition(conn)
        assert "'platform'" in scope_check_definition(conn) and "PLATFORM" not in scope_check_definition(conn)

        for module in _MIGRATIONS.values():  # a second run changes nothing (CHECKs aside, created once)
            module._rewrite_names(conn)
            module._refuse_unknown(conn)
        assert {key: _raw(conn, *key) for key in frozen} == {key: pairs[-1][1] for key, pairs in frozen.items()}

    with engine.begin() as conn:
        for module in reversed(list(_MIGRATIONS.values())):
            _run(conn, module.downgrade)
        assert _enum_checks(conn) == set()
        assert "PENDING" in _index_definition(conn)
        assert "PLATFORM" in scope_check_definition(conn)
        # Values stay values: step-2 code reads them.
        assert {key: _raw(conn, *key) for key in frozen} == {key: pairs[-1][1] for key, pairs in frozen.items()}


def test_a_string_that_is_neither_form_stops_the_upgrade_with_a_report(engine, seeded_in_name_form):
    with engine.begin() as conn:
        _set_raw(conn, "stock_item", "condition", "MYSTERY")
    with engine.begin() as conn, pytest.raises(RuntimeError) as raised:
        _run(conn, _MIGRATIONS["inventory"].upgrade)

    assert "stock_item.condition = 'MYSTERY' in 1 row(s)" in str(raised.value)
    with engine.connect() as conn:
        assert _enum_checks(conn) == set()  # the failed upgrade rolled back with its transaction


# --- KAN-183: the Vehicles list sorted by status, over the swept rows -------------

_FLEET = [
    ("F-000001", "VINKAN183AAAAAA01", VehicleStatus.STOLEN, CatalogueMatchStatus.UNVERIFIED),
    ("F-000002", "VINKAN183AAAAAA02", VehicleStatus.ACTIVE, CatalogueMatchStatus.MATCHED),
    ("F-000003", "VINKAN183AAAAAA03", VehicleStatus.SCRAPPED, CatalogueMatchStatus.UNVERIFIED),
    ("F-000004", "VINKAN183AAAAAA04", VehicleStatus.ACTIVE, CatalogueMatchStatus.UNVERIFIED),
    ("F-000005", "VINKAN183AAAAAA05", VehicleStatus.EXPORTED, CatalogueMatchStatus.MATCHED),
]


@pytest.mark.parametrize("field", ["vehicleStatus", "catalogueMatchStatus"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_a_status_sorted_walk_returns_every_vehicle_once_after_the_sweep(client, db_session, engine, field, direction):
    """KAN-183: with names beside values a status-sorted cursor walk looped
    forever (the reviewer's reproduction). KAN-175's sweep leaves one form
    and its CHECK keeps it that way, so the walk returns every row exactly
    once, in order."""

    for number, vin, status, match in _FLEET:
        db_session.add(VehicleMdm(vehicle_number=number, vin=vin, vehicle_status=status, catalogue_match_status=match))
    db_session.commit()
    with engine.begin() as conn:
        drop_enum_checks(conn)
        conn.execute(
            text(
                "UPDATE vehicle_mdm SET vehicle_status = upper(vehicle_status), "
                "catalogue_match_status = upper(catalogue_match_status) WHERE vehicle_number IN ('F-000001', 'F-000004')"
            )
        )
        mixed = conn.execute(text("SELECT count(DISTINCT vehicle_status = upper(vehicle_status)) FROM vehicle_mdm"))
        assert mixed.scalar_one() == 2  # both forms really are in the table
        _run(conn, _MIGRATIONS["vehicle"].upgrade)
    db_session.expire_all()

    numbers = [item["vehicleNumber"] for item in _walk(client, sort=f"{field}:{direction}", limit=1)]

    key = {"vehicleStatus": 2, "catalogueMatchStatus": 3}[field]
    expected = sorted(_FLEET, key=lambda row: row[key].value, reverse=direction == "desc")
    if direction == "desc":  # ties stay in id (insertion) order
        groups: dict = {}
        for row in expected:
            groups.setdefault(row[key], []).append(row)
        expected = [row for value in groups for row in sorted(groups[value], key=_FLEET.index)]
    assert numbers == [row[0] for row in expected]


# --- KAN-298: the constraint helpers read the current schema only ----------------

_SHADOW_TABLES = ("vehicle_mdm", "integration_connection")


def _shadow_enum_checks(conn) -> int:
    return conn.execute(
        text(
            "SELECT count(*) FROM pg_constraint WHERE connamespace = 'kan298_shadow'::regnamespace "
            "AND conname LIKE 'ck\\_%\\_enum'"
        )
    ).scalar_one()


def test_the_constraint_helpers_ignore_a_second_schema_holding_the_same_tables(engine):
    """A test database can hold a second copy of the tables in another schema
    (a restored dump, another checkout's schema), with the same constraint
    names. The helpers the migration tests use must read, and drop, the
    current schema's constraints only."""

    with engine.begin() as conn:
        public = enum_checks(conn)
        conn.execute(text("CREATE SCHEMA kan298_shadow"))
        for table in _SHADOW_TABLES:
            conn.execute(text(f"CREATE TABLE kan298_shadow.{table} (LIKE public.{table} INCLUDING ALL)"))
    try:
        with engine.begin() as conn:
            shadow = _shadow_enum_checks(conn)
            assert shadow == sum(1 for table, _name in public if table in _SHADOW_TABLES) > 0

            assert enum_checks(conn) == public
            assert "'platform'" in scope_check_definition(conn)
            assert drop_enum_checks(conn) == len(public)
            assert enum_checks(conn) == {}
            assert _shadow_enum_checks(conn) == shadow  # the copy's untouched
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA kan298_shadow CASCADE"))
