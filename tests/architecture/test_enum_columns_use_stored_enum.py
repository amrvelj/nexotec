"""KAN-86: every enum column is a StoredEnum (app/core/enum_type.py).

A bare `sqlalchemy.Enum` stores the member NAME and decodes nothing else —
the mismatch behind KAN-60 and KAN-91, where migrations written against
`.value` silently matched nothing or left undecodable rows. StoredEnum
carries the name→value move (and, until it completes, reads both forms);
a column that bypasses it would fall out of that move unnoticed.
"""

import ast
import pathlib

from sqlalchemy import Enum as SAEnum

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.enum_type import StoredEnum
from app.db import Base

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def test_no_mapped_column_is_a_bare_sqlalchemy_enum():
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, SAEnum)
    ]
    assert offenders == [], f"use StoredEnum(MyEnum, length=N) instead of sqlalchemy.Enum: {offenders}"


def test_every_enum_typed_column_is_a_stored_enum():
    """The other direction: at least the columns KAN-86 moved exist as
    StoredEnum, so the check above cannot pass by finding nothing."""

    stored = [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, StoredEnum)]
    assert len(stored) >= 62


def test_no_module_in_app_imports_sqlalchemy_enum():
    """Catches an Enum used outside a mapped model too (a Table(), a
    migration helper living in app/)."""

    offenders = []
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {"sqlalchemy", "sqlalchemy.types", "sqlalchemy.sql.sqltypes"}:
                if any(alias.name == "Enum" for alias in node.names):
                    offenders.append(str(path.relative_to(_APP.parent)))
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "Enum"
                and isinstance(node.value, ast.Name)
                and node.value.id in {"sa", "sqlalchemy"}
            ):
                offenders.append(str(path.relative_to(_APP.parent)))
    assert offenders == [], f"import StoredEnum from app.core.enum_type instead: {sorted(set(offenders))}"
