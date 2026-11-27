"""Server-side sort parameter parsing (U-02, UI/UX Core Principles §
FR-UI-01): `?sort=field:dir[,field:dir]`.

U-03 as amended by U-10 (Anto, 2026-10-26; ADR-179): every column is
sortable, and an index is added where a measurement against the budget says
so — no longer a gate on sortability. A grid on the shared list-query layer
(`app.core.list_query`, KAN-461) sorts through its column registry; the
hand-built allow-lists below serve the lists not yet on it.
"""

import dataclasses
import datetime as dt
import decimal
import uuid
from typing import Any

from sqlalchemy import literal
from sqlalchemy.sql.elements import Label
from sqlalchemy.types import Boolean, Numeric, TypeDecorator

from app.core.enum_type import StoredEnum
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.pagination import SortCursorPosition, SortPageParams, decode_sort_cursor, encode_sort_cursor
from app.core.text_collation import collated_sort_column
from app.core.types import GUID

_VALID_DIRECTIONS = {"asc", "desc"}

CURSOR_REFUSED = "Cursor is malformed or invalid."
CURSOR_REFUSED_REASON = "cursor_invalid"


@dataclasses.dataclass(frozen=True)
class SortField:
    api_name: str
    column: Any  # InstrumentedAttribute, or a text one wrapped to sort under the collation
    direction: str  # "asc" | "desc"
    nullable: bool

    def __post_init__(self) -> None:
        # KAN-740: a text column sorts in one order whatever the database's default collation
        # (`app.core.text_collation`), here so no list's sort field can miss it.
        object.__setattr__(self, "column", collated_sort_column(self.column))


def parse_sort(sort: str | None, *, allowed: dict[str, Any]) -> list[SortField]:
    """`allowed` maps the API-facing field name (camelCase, matching the
    response schema) to the SQLAlchemy column it sorts on. Unknown fields,
    malformed specs, and repeated fields are all `422`s — never a silent
    ignore, per the doc: "Unknown fields return 422, never a silent ignore."
    """

    if not sort:
        return []

    fields: list[SortField] = []
    seen: set[str] = set()
    for raw_part in sort.split(","):
        part = raw_part.strip()
        if not part:
            continue
        name, sep, direction = part.partition(":")
        if not sep:
            raise UnprocessableEntityError(
                f"Invalid sort spec '{part}' — expected 'field:asc' or 'field:desc'.", details={"sort": part}
            )
        column = allowed.get(name)
        if column is None:
            raise UnprocessableEntityError(
                f"Unknown sort field '{name}'.", details={"field": name, "allowed": sorted(allowed)}
            )
        if direction not in _VALID_DIRECTIONS:
            raise UnprocessableEntityError(
                f"Invalid sort direction '{direction}' for field '{name}' — must be 'asc' or 'desc'.",
                details={"field": name, "direction": direction},
            )
        if name in seen:
            raise UnprocessableEntityError(
                f"Sort field '{name}' was specified more than once.", details={"field": name}
            )
        seen.add(name)
        fields.append(
            SortField(
                api_name=name,
                column=column,
                direction=direction,
                nullable=_nullable(column),
            )
        )
    return fields


def _column_type(column: Any) -> Any:
    """The SQL type a sort field binds: a mapped column's own, or a derived
    expression's (a `Label`, KAN-786)."""

    return column.type if isinstance(column, Label) else column.property.columns[0].type


def _nullable(column: Any) -> bool:
    """A mapped column's own `nullable`; a derived SQL expression (a `Label`
    whose key names the value each row carries, KAN-786, or a read-only
    `column_property` over a scalar subquery, KAN-813) may be NULL, so counts
    as nullable."""

    if isinstance(column, Label):
        return True
    return bool(getattr(column.property.columns[0], "nullable", True))


# --- A sorted cursor answers the sort it is replayed with (KAN-182) ---------
#
# `app.core.pagination.decode_sort_cursor` (a preserved file) checks only that
# a cursor is well-formed base64 JSON of tagged values. It does not know the
# request's sort, so a cursor minted under one `?sort=` and replayed under
# another reached SQL with values of the wrong count or type: a StoredEnum
# bind raised `LookupError`, psycopg `operator does not exist` — a 500 for a
# client mistake. Every sorted list decodes its cursor here instead, against
# the sort fields it pages by (tests/architecture/
# test_sorted_cursor_is_decoded_against_its_sort.py): one value per sort
# field plus the row id, each of the type its column binds, an enum value a
# member of the column's enum, NULL only on a nullable column, no NUL
# character. Anything else is the taxonomy's 400 with `details.reason`
# `cursor_invalid`, a malformed cursor included. The wire format is unchanged.


def _bind_type(column_type: Any) -> Any:
    """The SQLAlchemy type a TypeDecorator binds through (UTCDateTime binds
    as DateTime); StoredEnum and GUID are judged as themselves."""

    while isinstance(column_type, TypeDecorator) and not isinstance(column_type, (StoredEnum, GUID)):
        column_type = column_type.impl_instance
    return column_type


def _cursor_python_type(column: Any) -> Any:
    """What a cursor value for `column` must be: a StoredEnum, GUID, or the
    Python type the column binds; None when the type cannot be judged."""

    column_type = _bind_type(_column_type(column))
    if isinstance(column_type, (StoredEnum, GUID)):
        return column_type
    try:
        return column_type.python_type
    except NotImplementedError:
        return None


_SUPPORTED_PYTHON_TYPES = (bool, int, decimal.Decimal, dt.datetime, dt.date, uuid.UUID, str)


def _numeric_scale(column: Any) -> int | None:
    """The fixed scale of a Numeric column (DECIMAL(12, 2) → 2); None when
    the column declares none, so no scaled integer is exact for it."""

    column_type = _bind_type(_column_type(column))
    scale = column_type.scale if isinstance(column_type, Numeric) else None
    return scale if isinstance(scale, int) else None


def cursor_column_supported(column: Any) -> bool:
    """Whether a sorted cursor can be minted and checked against `column` at
    all — the guard every sort allow-list is held to
    (tests/cross_context/test_sorted_cursor_must_match_sort.py): a column of another type
    would refuse every cursor, and a Numeric column without a fixed scale
    could not carry its value exactly (KAN-713)."""

    expected = _cursor_python_type(column)
    if expected is decimal.Decimal:
        return _numeric_scale(column) is not None
    return isinstance(expected, (StoredEnum, GUID)) or expected in _SUPPORTED_PYTHON_TYPES


def _cursor_value_fits(value: Any, column: Any) -> bool:
    """Whether a decoded, non-NULL cursor value (a datetime, UUID, bool, int
    or str — what the cursor's tags carry) is one `build_page_for_sort` can
    have minted for `column`: a Numeric column's scaled integer, a Date
    column's midnight datetime, otherwise a value of the column's own type."""

    expected = _cursor_python_type(column)
    if isinstance(expected, StoredEnum):
        return isinstance(value, str) and value in expected.values
    if isinstance(expected, GUID) or expected is uuid.UUID:
        return isinstance(value, uuid.UUID)
    if expected is bool:
        return isinstance(value, bool)
    if expected in (int, decimal.Decimal):
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is dt.datetime:
        return isinstance(value, dt.datetime)
    if expected is dt.date:
        return isinstance(value, dt.datetime) and value.time() == dt.time.min
    if expected is str:
        return isinstance(value, str) and "\x00" not in value
    return False


# --- Date and money sort values on the cursor (KAN-713) ---------------------
#
# The preserved serialiser (`app.core.pagination._serialize_sort_value`)
# writes None, datetime, UUID, bool, int and str — no `date`, no `Decimal`.
# A sort on a Date or Numeric column therefore raised TypeError minting its
# next cursor: page one answered, page two was a 500. Rather than add tags to
# the preserved file, the two types travel on tags it already has, and this
# module is the one place that converts them (ADR-179, KAN-713):
#   - a Numeric value as its scaled integer — value × 10^scale of its column,
#     so DECIMAL(12, 2)'s 12500.50 is ["int", 1250050]: exact, no float;
#   - a date as that day's midnight, a naive datetime — ["dt", "2026-03-01T00:00:00"].
# `decode_cursor_for_sort` turns both back into the column's own type before
# the keyset comparison binds them, and binds a boolean explicitly. The
# list-query layer (`app.core.list_query`) uses these two functions and
# converts nothing itself. Every sorted list mints its cursor here
# (`build_page_for_sort`), never with `pagination.build_sorted_page`
# (tests/architecture/test_sorted_cursor_is_decoded_against_its_sort.py).


def _to_cursor_value(value: Any, column: Any) -> Any:
    if value is None:
        return None
    expected = _cursor_python_type(column)
    if expected is decimal.Decimal:
        scale = _numeric_scale(column)
        if scale is None:
            raise TypeError(f"{column} has no fixed scale; a cursor cannot carry its value exactly.")
        scaled = decimal.Decimal(value).scaleb(scale)
        if not scaled.is_finite():
            raise TypeError(f"{value!r} on {column} is not a finite number; a cursor cannot carry it.")
        if scaled != scaled.to_integral_value():
            raise TypeError(f"{value!r} has more digits than the scale of {column}.")
        return int(scaled)
    if expected is dt.date and not isinstance(value, dt.datetime):
        return dt.datetime.combine(value, dt.time.min)
    return value


def _from_cursor_value(value: Any, column: Any) -> Any:
    if value is None:
        return None
    expected = _cursor_python_type(column)
    if expected is decimal.Decimal:
        scale = _numeric_scale(column)
        assert scale is not None  # cursor_column_supported holds every sort allow-list to it
        return decimal.Decimal(value).scaleb(-scale)
    if expected is dt.date:
        return value.date()
    if expected is bool:
        # `column > False` is refused by SQLAlchemy (a Python True/False/None
        # compares only with = or IS); bound, a boolean compares like any value
        # (false < true in Postgres). Found merging KAN-461 into KAN-713: the
        # users list sorted by isDealerManager was a 500 on page two.
        return literal(value, Boolean())
    return value


def build_page_for_sort(rows: list[Any], params: SortPageParams) -> tuple[list[Any], str | None]:
    """`app.core.pagination.build_sorted_page`, with each sort value carried
    as `decode_cursor_for_sort` reads it back: the page (at most `limit`
    rows of the `limit + 1` fetched) and the next cursor, None on the last page."""

    has_more = len(rows) > params.limit
    page_rows = rows[: params.limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        values = tuple(_to_cursor_value(getattr(last, field.column.key), field.column) for field in params.sort_fields)
        next_cursor = encode_sort_cursor(SortCursorPosition(values=values, id=last.id))
    return page_rows, next_cursor


def _refused() -> BadRequestError:
    return BadRequestError(CURSOR_REFUSED, details={"reason": CURSOR_REFUSED_REASON})


def decode_cursor_for_sort(cursor: str | None, sort_fields: list[SortField]) -> SortCursorPosition | None:
    """The position `cursor` names, checked against the sort the request
    pages by; None for no cursor. Refuses (400, `cursor_invalid`) a cursor
    that is malformed, that carries other than one value per sort field
    plus a UUID row id, or a value its column cannot bind. Each value comes
    back in its column's own type — a Numeric column's scaled integer as a
    Decimal, a Date column's midnight as a date (KAN-713) — ready to bind."""

    if not cursor:
        return None
    try:
        position = decode_sort_cursor(cursor)
    except BadRequestError as exc:
        raise _refused() from exc
    if not isinstance(position.id, uuid.UUID) or len(position.values) != len(sort_fields):
        raise _refused()
    for field, value in zip(sort_fields, position.values, strict=True):
        if value is None:
            if not field.nullable:
                raise _refused()
        elif not _cursor_value_fits(value, field.column):
            raise _refused()
    values = tuple(
        _from_cursor_value(value, field.column) for field, value in zip(sort_fields, position.values, strict=True)
    )
    return SortCursorPosition(values=values, id=position.id)
