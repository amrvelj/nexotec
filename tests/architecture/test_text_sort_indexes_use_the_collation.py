"""KAN-740 (ADR-190): a list sorts a word column under the text sort collation (`app.core.sorting.
SortField`); an index behind such a sort is built under the same collation
(`app.core.text_collation.text_sort_index_key`), or it serves neither the ORDER BY nor the keyset
cursor's `>` and every page becomes a full sort. A code or an identifier (`IDENTIFIER_TEXT`) keeps
byte order and its plain index.

The sort columns are read from every list's own declaration — the `parse_sort` allow-lists (a
dict of column attributes), the list-query layer's `ColumnSpec` registries and the module-level
`SortField` lists — in every `app` module the application imports, so a new list is covered
without being named here."""

import sys
from typing import Any

from sqlalchemy.orm.attributes import InstrumentedAttribute

import app.main  # noqa: F401  imports every router, so every list's declaration
from app.core.list_query import ColumnSpec
from app.core.sorting import SortField
from app.core.text_collation import TextSortKey, is_identifier_text, is_plain_text_column


def _sort_columns() -> dict[tuple[str, str], str]:
    """(table, column) -> where it is declared, for every word column a list sorts on."""

    found: dict[tuple[str, str], str] = {}

    def add(column: Any, where: str) -> None:
        attribute = getattr(column, "attribute", column)  # a SortField's column is wrapped
        if isinstance(attribute, InstrumentedAttribute) and is_plain_text_column(attribute):
            table_column = attribute.property.columns[0]
            found.setdefault((table_column.table.name, table_column.name), where)

    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("app.") or module_name.startswith("app.core."):
            continue
        for name, value in vars(module).items():
            where = f"{module_name}.{name}"
            if isinstance(value, dict):
                for column in value.values():
                    add(column, where)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, ColumnSpec) and item.sortable:
                        add(item.sort_column if item.sort_column is not None else item.column, where)
                    elif isinstance(item, SortField):
                        add(item.column, where)
    return found


def test_the_scan_finds_the_lists_it_guards():
    columns = _sort_columns()
    for expected in (("contact", "last_name"), ("contact", "company_name"), ("sales_deal", "owner_label"), ("stock_item", "vehicle_label")):
        assert expected in columns, expected


def test_every_index_behind_a_word_sort_is_built_under_the_collation():
    from app.db import Base

    offending = []
    for (table_name, column_name), where in sorted(_sort_columns().items()):
        table = Base.metadata.tables[table_name]
        column = table.c[column_name]
        for index in table.indexes:
            plain = [e for e in index.expressions if e is column]
            if plain:
                offending.append(f"{index.name} on {table_name}.{column_name} (sorted by {where})")
        if column.index:
            offending.append(f"index=True on {table_name}.{column_name} (sorted by {where})")
    assert not offending, (
        "An index behind a word sort must be built under the text sort collation "
        "(`text_sort_index_key`, KAN-740) — or the column is a code (`IDENTIFIER_TEXT`):\n" + "\n".join(offending)
    )


def test_an_identifier_column_has_no_collated_index():
    """The other direction: a code keeps byte order, and so does its index (its exact lookups)."""

    from app.db import Base

    offending = [
        f"{index.name} on {table.name}"
        for table in Base.metadata.tables.values()
        for index in table.indexes
        for expression in index.expressions
        if isinstance(expression, TextSortKey) and is_identifier_text(expression.column)
    ]
    assert not offending, offending
