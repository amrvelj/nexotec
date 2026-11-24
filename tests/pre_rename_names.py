"""Today's contact tables under the names they had before KAN-464 PR-3.

A migration on `main` is immutable, and the ones written before customers
became contacts (ADR-209) name `customer`, `customer_phone`, `customer_id`, ….
A test that calls such a migration's function against today's schema runs it
inside `pre_rename_names(connection)`: each renamed table is shown under its old
name, with its renamed columns under their old names, as a temporary view.
Postgres writes through a simple view (an auto-updatable view), so the old
migration's SELECT, INSERT and UPDATE reach today's rows, and the test asserts
on them through today's models. The views live in the connection's own
temporary schema, which is searched first, and are dropped on exit.

The names come from the rename migration itself, so this map and the schema
cannot drift apart.
"""

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa

_RENAME = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "customer" / "f0127c9deaf6_kan464_contact_rename.py"
)


def _load_rename():
    spec = importlib.util.spec_from_file_location("kan464_contact_rename", _RENAME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_rename()
_TABLES: tuple[tuple[str, str], ...] = _MIGRATION._TABLES
_COLUMNS: tuple[tuple[str, str, str], ...] = _MIGRATION._COLUMNS


@contextmanager
def pre_rename_names(connection: sa.Connection) -> Iterator[None]:
    old_column = {(table, new): old for table, old, new in _COLUMNS}
    schema = connection.execute(sa.text("SELECT current_schema()")).scalar_one()
    inspector = sa.inspect(connection)
    views = []
    for old_table, new_table in _TABLES:
        columns = [
            f'"{column["name"]}" AS "{old_column.get((new_table, column["name"]), column["name"])}"'
            for column in inspector.get_columns(new_table)
        ]
        connection.execute(sa.text(f'CREATE TEMPORARY VIEW "{old_table}" AS SELECT {", ".join(columns)} FROM "{new_table}"'))
        views.append(old_table)
    # The two tables whose columns were renamed in place (KAN-464 kept their names).
    for table in sorted({table for table, _old, _new in _COLUMNS} - {new for _old, new in _TABLES}):
        columns = [
            f'"{column["name"]}" AS "{old_column.get((table, column["name"]), column["name"])}"'
            for column in inspector.get_columns(table)
        ]
        connection.execute(sa.text(f'CREATE TEMPORARY VIEW "{table}" AS SELECT {", ".join(columns)} FROM "{schema}"."{table}"'))
        views.append(table)
    try:
        yield
    finally:
        for view in reversed(views):
            connection.execute(sa.text(f'DROP VIEW IF EXISTS pg_temp."{view}"'))


# --- the whole schema under its old names, DDL included ---------------------

_RENAME_MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*/*_kan464_contact_rename.py"))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"kan464_{path.parent.name}_rename", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCHEMA_RENAMES = [_load(path) for path in _RENAME_MIGRATIONS]
_NEW_TABLE = dict(_TABLES)
_NEW_COLUMN = {
    (table, old): new for module in _SCHEMA_RENAMES for table, old, new in getattr(module, "_COLUMNS", ())
}


def renamed(table: str, column: str | None = None) -> tuple[str, str | None]:
    """A (table, column) a pre-rename migration names, as today's schema names it."""

    new_table = _NEW_TABLE.get(table, table)
    return new_table, None if column is None else _NEW_COLUMN.get((new_table, column), column)


def _rename_schema(connection: sa.Connection, *, back: bool) -> None:
    def pick(old: str, new: str) -> tuple[str, str]:
        return (new, old) if back else (old, new)

    def exists(kind: str, name: str) -> bool:
        query = {
            "constraint": "SELECT 1 FROM pg_constraint WHERE conname = :name AND connamespace = current_schema()::regnamespace",
            "index": "SELECT 1 FROM pg_indexes WHERE indexname = :name AND schemaname = current_schema()",
        }[kind]
        return connection.execute(sa.text(query), {"name": name}).first() is not None

    tables = [(old, new) for module in _SCHEMA_RENAMES for old, new in getattr(module, "_TABLES", ())]
    columns = [row for module in _SCHEMA_RENAMES for row in getattr(module, "_COLUMNS", ())]
    constraints = [row for module in _SCHEMA_RENAMES for row in getattr(module, "_CONSTRAINTS", ())]
    indexes = [row for module in _SCHEMA_RENAMES for row in getattr(module, "_INDEXES", ())]
    table_now = {new: old for old, new in tables} if back else {}
    if not back:
        for old, new in tables:
            connection.execute(sa.text(f'ALTER TABLE "{old}" RENAME TO "{new}"'))
    for table, old, new in columns:
        source, target = pick(old, new)
        connection.execute(sa.text(f'ALTER TABLE "{table}" RENAME COLUMN "{source}" TO "{target}"'))
    for table, old, new in constraints:
        source, target = pick(old, new)
        if exists("constraint", source):  # a test may have dropped one (the enum CHECKs)
            connection.execute(sa.text(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{source}" TO "{target}"'))
    for old, new in indexes:
        source, target = pick(old, new)
        if exists("index", source):
            connection.execute(sa.text(f'ALTER INDEX "{source}" RENAME TO "{target}"'))
    if back:
        for new, old in table_now.items():
            connection.execute(sa.text(f'ALTER TABLE "{new}" RENAME TO "{old}"'))


@contextmanager
def pre_rename_schema(connection: sa.Connection) -> Iterator[None]:
    """For a pre-rename migration that alters tables too (a view takes no
    DDL): every table, column, index and constraint the KAN-464 PR-3 rename
    migrations renamed is put back under its old name in the caller's
    transaction (their own lists, so they cannot drift), the block runs as on
    the database it was written for, and everything is renamed forward
    again — the order production ran them in. A constraint the test already
    dropped is skipped. Test isolation rebuilds a schema a test altered."""

    _rename_schema(connection, back=True)
    try:
        yield
    finally:
        _rename_schema(connection, back=False)
