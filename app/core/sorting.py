"""Server-side sort parameter parsing (U-02, UI/UX Core Principles §
FR-UI-01): `?sort=field:dir[,field:dir]`.

"Every column declared sortable must have a supporting index. A column
with no index is not shipped as sortable" (U-03) — that rule is enforced by
what an entity's allow-list chooses to expose, not by this module; see
app/api/v1/customers.py's CUSTOMER_SORT_FIELDS for where that judgment call
is made and which migration backs each column.
"""

import dataclasses
import datetime as dt
import decimal
import uuid
from typing import Any

from sqlalchemy.types import TypeDecorator

from app.core.enum_type import StoredEnum
from app.core.errors import BadRequestError, UnprocessableEntityError
from app.core.pagination import SortCursorPosition, decode_sort_cursor
from app.core.types import GUID

_VALID_DIRECTIONS = {"asc", "desc"}

CURSOR_REFUSED = "Cursor is malformed or invalid."
CURSOR_REFUSED_REASON = "cursor_invalid"


@dataclasses.dataclass(frozen=True)
class SortField:
    api_name: str
    column: Any  # InstrumentedAttribute
    direction: str  # "asc" | "desc"
    nullable: bool


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
                nullable=column.property.columns[0].nullable,
            )
        )
    return fields


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

    column_type = _bind_type(column.property.columns[0].type)
    if isinstance(column_type, (StoredEnum, GUID)):
        return column_type
    try:
        return column_type.python_type
    except NotImplementedError:
        return None


_SUPPORTED_PYTHON_TYPES = (bool, int, decimal.Decimal, dt.datetime, dt.date, uuid.UUID, str)


def cursor_column_supported(column: Any) -> bool:
    """Whether a sorted cursor can be checked against `column` at all — the
    guard every sort allow-list is held to (tests/test_sorted_cursor_must_match_sort.py):
    a column of another type would refuse every cursor."""

    expected = _cursor_python_type(column)
    return isinstance(expected, (StoredEnum, GUID)) or expected in _SUPPORTED_PYTHON_TYPES


def _cursor_value_fits(value: Any, column: Any) -> bool:
    """Whether a decoded, non-NULL cursor value (a datetime, UUID, bool, int
    or str — what the cursor's tags carry) binds into a comparison against
    `column` without a database error."""

    expected = _cursor_python_type(column)
    if isinstance(expected, StoredEnum):
        return isinstance(value, str) and value in expected.values
    if isinstance(expected, GUID) or expected is uuid.UUID:
        return isinstance(value, uuid.UUID)
    if expected is bool:
        return isinstance(value, bool)
    if expected in (int, decimal.Decimal):
        return isinstance(value, int) and not isinstance(value, bool)
    if expected in (dt.datetime, dt.date):
        return isinstance(value, dt.datetime)
    if expected is str:
        return isinstance(value, str) and "\x00" not in value
    return False


def _refused() -> BadRequestError:
    return BadRequestError(CURSOR_REFUSED, details={"reason": CURSOR_REFUSED_REASON})


def decode_cursor_for_sort(cursor: str | None, sort_fields: list[SortField]) -> SortCursorPosition | None:
    """The position `cursor` names, checked against the sort the request
    pages by; None for no cursor. Refuses (400, `cursor_invalid`) a cursor
    that is malformed, that carries other than one value per sort field
    plus a UUID row id, or a value its column cannot bind."""

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
    return position
