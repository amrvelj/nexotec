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


def index_definition_differences(connection) -> list[str]:
    """Every index both sides have, by name, whose stored definition differs.
    An index only one side has is not reported here: a plain index is
    `compare_metadata`'s add_index/remove_index, alembic_version exists only
    on the migrated side, and an index backing a primary key or unique
    constraint that the two sides name differently is matched by
    `compare_metadata` on its columns — its definition is not compared
    (KAN-431 tracks that name drift)."""

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
    return [
        f"modify_index: {table}.{index}: migrated {migrated[(table, index)]!r}, model {models[(table, index)]!r}"
        for table, index in sorted(migrated.keys() & models.keys())
        if migrated[(table, index)] != models[(table, index)]
    ]


def schema_differences(connection) -> list[str]:
    context = MigrationContext.configure(connection, opts=dict(COMPARE_OPTS))
    found = [describe(diff) for diff in flatten(compare_metadata(context, Base.metadata))]
    return sorted(found + index_definition_differences(connection))


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
