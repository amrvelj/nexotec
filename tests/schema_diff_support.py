"""The schema comparison behind tests/test_migrated_schema_matches_models.py
(KAN-170 / KAN-251): every way a database differs from the models, as
`alembic check` sees it, one readable line each.

Run as a program (`python -m tests.schema_diff_support <sqlalchemy url>`) it
prints that list as JSON. The gate test runs it that way, in a fresh
interpreter, so the comparison sees app.model_registry's metadata and
nothing else, exactly as alembic/env.py does, whatever the pytest process has
imported. Since KAN-332 the test-only demo_widget sits on tests/demo_models.py's
own base, and in-process Base.metadata holds the same tables; the fresh
interpreter is kept as a guard, not because anything differs today.

`compare_metadata` does not compare an index's predicate: a partial index
recreated without its `WHERE`, or with another one, passes it (KAN-414).
`index_definition_differences` closes that gap by comparing every index as
Postgres stores it (`pg_get_indexdef`: columns, uniqueness, method, WHERE)
against the same index built by the models' `create_all` into a scratch
schema, inside a transaction that is rolled back — the inspected database is
left exactly as it was.
"""

import json
import re
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Index, create_engine, text

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.db import Base

# Mirrors the context.configure(...) call in alembic/env.py: the same
# target_metadata, no include_object filter, Alembic's default compare_type
# (on) and compare_server_default (off). Change both together.
COMPARE_OPTS = {"target_metadata": Base.metadata, "compare_type": True, "compare_server_default": False}


def describe(diff: tuple) -> str:
    kind = diff[0]
    if kind in ("add_table", "remove_table"):
        return f"{kind}: {diff[1].name}"
    if kind in ("add_column", "remove_column"):
        return f"{kind}: {diff[2]}.{diff[3].name}"
    if kind in ("add_index", "remove_index"):
        index: Index = diff[1]
        unique = "unique " if index.unique else ""
        columns = ", ".join(str(expression) for expression in index.expressions)
        return f"{kind}: {unique}index {index.name} on {index.table.name} ({columns})"
    if kind in ("add_constraint", "remove_constraint"):
        constraint = diff[1]
        columns = ", ".join(column.name for column in constraint.columns)
        return f"{kind}: {type(constraint).__name__} {constraint.name} on {constraint.table.name} ({columns})"
    if kind in ("add_fk", "remove_fk"):
        fk = diff[1]
        return f"{kind}: {fk.name} on {fk.table.name}"
    if kind == "add_table_comment":
        return f"{kind}: {diff[1].name} -> {diff[1].comment!r}"
    if kind == "remove_table_comment":
        return f"{kind}: {diff[1].name} (migrated has one, the model has none)"
    if kind.startswith("modify_"):
        _, _schema, table, column, _info, migrated, model = diff
        return f"{kind}: {table}.{column}: migrated {migrated!r}, model {model!r}"
    return f"{kind}: {diff[1:]}"


def flatten(diffs: list) -> list[tuple]:
    flat: list[tuple] = []
    for diff in diffs:
        if isinstance(diff, list):
            flat.extend(flatten(diff))
        else:
            flat.append(diff)
    return flat


# Where the models' own indexes are built for the comparison; never committed.
_MODELS_SCHEMA = "kan414_models_reference"


def _index_definitions(connection, schema: str) -> dict[tuple[str, str], str]:
    """(table, index) -> `pg_get_indexdef` for every index in `schema`, with
    that schema's qualifier (and `public.`, where extension operator classes
    live) taken out so the same index reads the same from either schema."""

    qualifier = re.compile(rf'(?<![\w"])(?:"?{re.escape(schema)}"?|public)\.')
    rows = connection.execute(
        text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = :schema"), {"schema": schema}
    )
    return {(table, index): qualifier.sub("", definition) for table, index, definition in rows}


def index_definition_differences(connection, reported: frozenset[str] = frozenset()) -> list[str]:
    """Every index both sides have, by name, whose stored definition differs,
    and every index only one side has on a table both sides have (KAN-431).

    `compare_metadata` matches an index backing a primary key or unique
    constraint by its columns, so a constraint the two sides name
    differently (`dealer_pkey` against `dealership_pkey`) passed it, and its
    definition was never compared here either. Reported by name instead, a
    name drift goes red. An index or constraint `compare_metadata` already
    reported (`reported`, by name: a plain index or a unique constraint one
    side lacks) is not said twice; alembic_version, a table the models do
    not have, is left out by the both-sides rule."""

    if connection.in_transaction():
        connection.rollback()  # compare_metadata's reads; the scratch schema needs its own transaction
    transaction = connection.begin()
    try:
        migrated = _index_definitions(connection, connection.execute(text("SELECT current_schema()")).scalar_one())
        connection.execute(text(f'CREATE SCHEMA "{_MODELS_SCHEMA}"'))
        Base.metadata.create_all(connection.execution_options(schema_translate_map={None: _MODELS_SCHEMA}))
        models = _index_definitions(connection, _MODELS_SCHEMA)
    finally:
        transaction.rollback()
    changed = [
        f"modify_index: {table}.{index}: migrated {migrated[(table, index)]!r}, model {models[(table, index)]!r}"
        for table, index in sorted(migrated.keys() & models.keys())
        if migrated[(table, index)] != models[(table, index)]
    ]
    tables = {table for table, _ in migrated} & {table for table, _ in models}
    one_sided = [
        f"{side}_index_by_name: {table}.{index}: {definitions[(table, index)]!r}"
        for side, definitions, other in (("migrated_only", migrated, models), ("model_only", models, migrated))
        for table, index in sorted(definitions.keys() - other.keys())
        if table in tables and index not in reported
    ]
    return changed + one_sided


def schema_differences(connection) -> list[str]:
    context = MigrationContext.configure(connection, opts=dict(COMPARE_OPTS))
    diffs = flatten(compare_metadata(context, Base.metadata))
    reported = frozenset(
        diff[1].name
        for diff in diffs
        if diff[0] in ("add_index", "remove_index", "add_constraint", "remove_constraint") and diff[1].name
    )
    return sorted([describe(diff) for diff in diffs] + index_definition_differences(connection, reported))


def main(url: str, *, fail: bool = False) -> int:
    """Prints the differences as a JSON list. With ``fail`` (CI's migration
    smoke test, KAN-251) the exit status is 1 when there is any."""

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            found = schema_differences(conn)
    finally:
        engine.dispose()
    print(json.dumps(found))
    return 1 if (fail and found) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], fail="--fail" in sys.argv[2:]))
