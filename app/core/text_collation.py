"""The order a text column sorts in (KAN-740; Anto, 2026-11-18: one order for every UI language).

Case is ignored and an accented or umlauted letter sits beside its base letter — «alpha, Arzt,
Ärztin, école, Ezra, Zeta» — the same in DE, FR, IT and EN, whatever collation the database was
created with (the cloud test database runs `C.UTF-8`, byte order; staging's and production's is
whatever the instance was created with). It is the ICU root collation at strength 2
(`und-u-ks-level2`), created as `TEXT_SORT_COLLATION` by the core migration chain and, for a
schema built with `create_all` (the test harness), by `CREATE_TEXT_SORT_COLLATION` — the one DDL
both use. The collation is deterministic: two values equal under it are then ordered by their
bytes, so a keyset cursor's `=` and `<` still name exactly one position.

`collated_sort_column` wraps a mapped text column so `app.core.pagination.paginate_query_sorted`
(preserved, unchanged) orders and compares it under the collation — the ORDER BY and the
cursor's keyset predicate together, or a page boundary would fall between two orders — while
`.key` and `.property` still name the mapped attribute the cursor reads its value from.
`app.core.sorting.SortField` applies it to every plain text column it is given, so every sorted
list (the list-query layer's grids and the hand-built allow-lists) sorts the same way — except a
column declared `IDENTIFIER_TEXT` (a code or a number, ADR-190), which keeps byte order. An index
that backs a sort on a word column is declared over `text_sort_index_key` so it serves the
collated order (`tests/architecture/test_text_sort_indexes_use_the_collation.py`).

On SQLite (the fast lane, never verification — ADR-011) the wrapper compiles to the bare column:
SQLite has no ICU collation.

Cross-cutting: imports no bounded context.
"""

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from sqlalchemy import DDL, String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.visitors import InternalTraversal
from sqlalchemy.types import Text, TypeDecorator

TEXT_SORT_COLLATION = "nexotec_text_sort"
TEXT_SORT_LOCALE = "und-u-ks-level2"

CREATE_TEXT_SORT_COLLATION = DDL(
    f'CREATE COLLATION IF NOT EXISTS "{TEXT_SORT_COLLATION}" '
    f"(provider = icu, locale = '{TEXT_SORT_LOCALE}', deterministic = true)"
).execute_if(dialect="postgresql")


class TextSortKey(ColumnElement[str]):
    """`<column> COLLATE "nexotec_text_sort"` on Postgres, the bare column elsewhere."""

    inherit_cache = True
    _traverse_internals = [("column", InternalTraversal.dp_clauseelement)]  # noqa: RUF012 - SQLAlchemy reads it per instance

    def __init__(self, column: Any) -> None:
        self.column = column.expression if hasattr(column, "expression") else column
        self.type = self.column.type


@compiles(TextSortKey)
def _compile_collated(element: TextSortKey, compiler: Any, **kw: Any) -> str:
    return f'{compiler.process(element.column, **kw)} COLLATE "{TEXT_SORT_COLLATION}"'


@compiles(TextSortKey, "sqlite")
def _compile_bare(element: TextSortKey, compiler: Any, **kw: Any) -> str:
    return compiler.process(element.column, **kw)


class CollatedSortColumn:
    """A mapped text attribute as a sort column: every expression it builds (`asc`, `desc`,
    `is_`, the comparisons) is on the collated key; `key` and `property` are the attribute's."""

    def __init__(self, attribute: Any) -> None:
        self.attribute = attribute
        self.sort_key = TextSortKey(attribute)

    @property
    def key(self) -> str:
        return self.attribute.key

    @property
    def property(self) -> Any:
        return self.attribute.property

    def asc(self) -> Any:
        return self.sort_key.asc()

    def desc(self) -> Any:
        return self.sort_key.desc()

    def is_(self, other: Any) -> Any:
        return self.sort_key.is_(other)

    def __lt__(self, other: Any) -> Any:
        return self.sort_key < other

    def __gt__(self, other: Any) -> Any:
        return self.sort_key > other

    def __le__(self, other: Any) -> Any:
        return self.sort_key <= other

    def __ge__(self, other: Any) -> Any:
        return self.sort_key >= other

    def __eq__(self, other: object) -> Any:  # type: ignore[override]
        return self.sort_key == other

    def __ne__(self, other: object) -> Any:  # type: ignore[override]
        return self.sort_key != other

    __hash__ = object.__hash__

    def __repr__(self) -> str:
        return f"CollatedSortColumn({self.attribute!r})"


# A text column that holds a code or an identifier, not words — a VIN, a Stammnummer, a stock,
# deal, offer, contract, valuation, customer or employee number, a phone number. It keeps the
# database's byte order: «case ignored, Ä beside A» means nothing for `WVWZZZ…` or `S-00012`, and
# its indexes keep serving the exact lookups they exist for (an index under another collation
# serves no `=` in the default one). Declared on the model's column:
# `mapped_column(String(17), info=IDENTIFIER_TEXT)`.
IDENTIFIER_TEXT: dict[str, Any] = {"text_sort": "identifier"}


def is_identifier_text(column: Any) -> bool:
    """The column (a mapped attribute or a `Column`) is declared `IDENTIFIER_TEXT`."""

    prop = getattr(column, "property", None)
    columns = getattr(prop, "columns", None) or [column]
    return getattr(columns[0], "info", {}).get("text_sort") == IDENTIFIER_TEXT["text_sort"]


def is_plain_text_column(column: Any) -> bool:
    """A mapped attribute over a `String`/`Text` column of its own type that holds words — not a
    `TypeDecorator` (a stored enum's code, a GUID, an encrypted value), whose order is not a
    word's, and not one declared `IDENTIFIER_TEXT`."""

    prop = getattr(column, "property", None)
    columns = getattr(prop, "columns", None)
    if not columns:
        return False
    column_type = columns[0].type
    return (
        isinstance(column_type, String)
        and not isinstance(column_type, TypeDecorator)
        and not is_identifier_text(column)
    )


def text_sort_index_key(column: Any) -> TextSortKey:
    """An index expression over `column` under the collation, for an index that backs a sort on a
    word column (`Index("ix_…", text_sort_index_key(Model.__table__.c.name))`): an index in the
    database's default collation serves neither the collated ORDER BY nor the keyset `>`, so every
    page would be a full sort. On SQLite it is the bare column, like the sort itself."""

    return TextSortKey(column)


def collated_sort_column(column: Any) -> Any:
    """`column` wrapped to sort under `TEXT_SORT_COLLATION` when it is a plain text column;
    anything else — or a column already wrapped — unchanged."""

    if isinstance(column, CollatedSortColumn) or not is_plain_text_column(column):
        return column
    return CollatedSortColumn(column)


_Item = TypeVar("_Item")


def sort_by_text(db: Session, items: Iterable[_Item], key: Callable[[_Item], str]) -> list[_Item]:
    """`items` in the order their `key` sorts under the collation, for a list that is not a
    query's rows (a provider's live answer, KAN-769): the database is asked for the order, so it
    is exactly the ORDER BY's — never a Python key (`.lower()` puts «Š…» after «Z», and any
    mirror drifts from ICU on punctuation). Equal keys keep their given order. One statement,
    none for an empty list; on SQLite (the fast lane) Python's byte order, as the sorts there."""

    listed = list(items)
    if not listed:
        return listed
    if db.get_bind().dialect.name != "postgresql":
        return sorted(listed, key=key)
    statement = text(
        "SELECT n FROM unnest(:values) WITH ORDINALITY AS t(v, n) "
        f'ORDER BY v COLLATE "{TEXT_SORT_COLLATION}", n'
    ).bindparams(bindparam("values", type_=ARRAY(Text())))
    positions = db.scalars(statement, {"values": [key(item) for item in listed]}).all()
    return [listed[position - 1] for position in positions]


def _is_text_sort_index(index: Any) -> bool:
    return any(isinstance(expression, TextSortKey) for expression in getattr(index, "expressions", ()))


def autogenerate_include_object(metadata: Any) -> Any:
    """The `include_object` filter Alembic's autogenerate and `alembic check` run with: every
    object, except the indexes built under the collation (`text_sort_index_key`), on both sides.

    SQLAlchemy's index reflection reads a plain column's name and drops its collation (Postgres
    keeps it in `pg_index.indcollation`), so autogenerate would always see the migrated
    `(last_name COLLATE …)` as `(last_name)` and report a drop-and-create that is not there.
    These indexes are compared exactly instead, as Postgres stores them (`pg_get_indexdef`, by
    name, against the models built into a scratch schema: `tests/schema_diff_support.py`), so a
    missing collation, a wrong column or an index only one side has still goes red. Such an index
    is a hand-written migration anyway: on a hot table it is built CONCURRENTLY."""

    def names() -> set[str]:
        return {
            index.name
            for table in metadata.tables.values()
            for index in table.indexes
            if index.name and _is_text_sort_index(index)
        }

    def include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
        if type_ != "index":
            return True
        if reflected:
            return name not in names()
        return not _is_text_sort_index(obj)

    return include_object
