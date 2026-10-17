"""The schema comparison behind tests/test_migrated_schema_matches_models.py
(KAN-170 / KAN-251): every way a database differs from the models, as
`alembic check` sees it, one readable line each.

Run as a program (`python -m tests.schema_diff_support <sqlalchemy url>`) it
prints that list as JSON. The gate test runs it that way, in a fresh
interpreter, because inside the pytest process Base.metadata carries
whatever app.main and the other test modules pulled in — the comparison must
see app.model_registry's metadata and nothing else, exactly as alembic/env.py
does. (The test-only demo_widget is no longer a reason: since KAN-332 it sits
on tests/demo_models.py's own base, never on Base.metadata.)
"""

import json
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Index, create_engine

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


def schema_differences(connection) -> list[str]:
    context = MigrationContext.configure(connection, opts=dict(COMPARE_OPTS))
    return sorted(describe(diff) for diff in flatten(compare_metadata(context, Base.metadata)))


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
