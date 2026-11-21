"""The shared list-query layer (KAN-461; FR-UI-01, ADR-058, ADR-060, U-02,
U-09, U-10 — Anto, 2026-10-26: every column on every grid is sortable and
filterable; indexes where measurement shows a need, never a gate on the
feature).

An entity declares one **column registry** — a tuple of `ColumnSpec`: the API
field, the SQL column, its value type, the operators that type allows, its
label key and whether the grid shows it by default. From that one
declaration:

- `?sort=field:dir[,field:dir]` sorts by any registry field
  (`parse_list_sort`), nulls last, the row id as the tiebreaker
  (`app.core.pagination.paginate_query_sorted`, unchanged);
- `?filter=field:operator[:value]` (repeatable; predicates are ANDed)
  filters by any registry field (`parse_filters`, `filter_clauses`);
- `GET …/grid-schema` serves the registry (`grid_schema`), and the SPA
  derives its columns, their sortability and its filter fields from it —
  one source of truth (exit criterion 2).

Anything outside the registry is a 422 with `details.reason`
(`unknown_field`, `operator_not_allowed`, `filter_malformed`,
`invalid_filter_value`, `field_not_sortable`), never a silent ignore.

**Scope is the caller's, and mandatory** (rule 7): `run_list_query` takes
the entity's scope predicates as a required, non-empty argument and this
module never names a tenant or a group column. Relative and absolute dates
resolve on the server in the session time zone (`app.core.relative_dates`).

**Cursor** (KAN-182 kept): `app.core.sorting` mints and decodes the keyset
cursor for every sorted list, this layer included (ADR-179's addendum,
KAN-713): a money or decimal value travels as its scaled integer, a calendar
date as midnight, a boolean is bound on decode — the preserved wire format —
and a cursor from another sort is still a 400 `cursor_invalid`. This module
converts nothing itself.

Cross-cutting: imports no bounded context.
"""

import dataclasses
import datetime as dt
import decimal
import enum as _enum
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, func, or_, true
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.core.pagination import SortPageParams, count_capped, paginate_query_sorted
from app.core.relative_dates import start_of_current_period, start_of_day, start_of_day_days_ago, today_in_session_zone
from app.core.schemas import CamelModel
from app.core.sorting import SortField, build_page_for_sort, decode_cursor_for_sort

ValueType = Literal["text", "enum", "number", "money", "date", "datetime", "boolean"]

_TEXT_OPERATORS = ("contains", "equals", "notEquals")
_ENUM_OPERATORS = ("is", "isNot", "in")
_NUMBER_OPERATORS = ("equals", "greaterThan", "lessThan", "atLeast", "atMost", "between")
# U-09 (KAN-474): the relative conditions; KAN-461 (Anto, 2026-11-15): «am»,
# «vor», «nach» a calendar date beside them.
_DATE_OPERATORS = (
    "today", "thisWeek", "thisMonth", "thisYear", "inTheLastDays", "moreThanDaysAgo", "on", "before", "after",
)

OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "text": _TEXT_OPERATORS,
    "enum": _ENUM_OPERATORS,
    "number": _NUMBER_OPERATORS,
    "money": _NUMBER_OPERATORS,
    "date": _DATE_OPERATORS,
    "datetime": _DATE_OPERATORS,
    "boolean": ("is",),
}

# The conditions that carry no value of their own.
_VALUELESS = frozenset({"today", "thisWeek", "thisMonth", "thisYear"})
_PERIOD_OF = {"today": "day", "thisWeek": "week", "thisMonth": "month", "thisYear": "year"}

# The bounds every N-days condition takes (the customer list's own, KAN-474).
MAX_FILTER_DAYS = 36500
MAX_TEXT_FILTER_LENGTH = 200
# The calendar days a date filter takes: a veteran car's first registration
# (KAN-535 accepts pre-1981 cars) is well inside; the bounds keep the day
# arithmetic and the UTC bind clear of year 1 and year 9999, which overflow.
MIN_FILTER_YEAR = 1800
MAX_FILTER_YEAR = 2999
_NUMBER = re.compile(r"^-?\d{1,12}(\.\d{1,4})?$")
_MONEY = re.compile(r"^-?\d{1,10}(\.\d{1,2})?$")
_DAYS = re.compile(r"^\d{1,5}$")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclasses.dataclass(frozen=True)
class ColumnSpec:
    """One grid column of an entity: what the API calls it, the mapped
    column it reads, sorts and filters on, its value type, and how the grid
    names and shows it.

    - `enum`: the Python enum of an `enum` column (its `.value`s are the
      stored codes the filter takes and the schema lists).
    - `enum_label_prefix`: the i18n key prefix of an enum's option labels
      (`<prefix>.<code>`).
    - `operators`: a narrower set than the type allows — only with a reason
      in `operators_reason` (the PRD's justification, FR-UI-01).
    - `sortable=False` needs `not_sortable_reason` (FR-UI-01).
    - A derived column (no column of its own, e.g. a bucket computed on
      read) sorts by `sort_column` (inverted with `sort_inverted`) and
      filters through `filter_builder(operator, value)`.
    """

    api_name: str
    column: Any
    value_type: ValueType
    label_key: str
    default_visible: bool = False
    enum: type[_enum.Enum] | None = None
    enum_label_prefix: str | None = None
    operators: tuple[str, ...] | None = None
    operators_reason: str | None = None
    sortable: bool = True
    not_sortable_reason: str | None = None
    sort_column: Any = None
    sort_inverted: bool = False
    filter_builder: Callable[[str, Any], ColumnElement[bool]] | None = None

    def __post_init__(self) -> None:
        if self.value_type == "enum" and (self.enum is None or self.enum_label_prefix is None):
            raise ValueError(f"{self.api_name}: an enum column names its enum and its label prefix")
        if not self.sortable and not self.not_sortable_reason:
            raise ValueError(f"{self.api_name}: a column that is not sortable says why (FR-UI-01)")
        allowed = OPERATORS_BY_TYPE[self.value_type]
        if self.operators is not None:
            if not self.operators_reason:
                raise ValueError(f"{self.api_name}: a narrowed operator set says why")
            if not set(self.operators) <= set(allowed):
                raise ValueError(f"{self.api_name}: operators outside its type's set")
        if self.column is None and (self.sort_column is None or self.filter_builder is None):
            raise ValueError(f"{self.api_name}: a derived column names its sort column and filter")

    @property
    def allowed_operators(self) -> tuple[str, ...]:
        return self.operators if self.operators is not None else OPERATORS_BY_TYPE[self.value_type]

    @property
    def enum_codes(self) -> tuple[str, ...]:
        assert self.enum is not None
        return tuple(str(member.value) for member in self.enum)


Registry = tuple[ColumnSpec, ...]


def by_name(registry: Registry) -> dict[str, ColumnSpec]:
    names = [spec.api_name for spec in registry]
    if len(names) != len(set(names)):
        raise ValueError("a registry names each field once")
    return {spec.api_name: spec for spec in registry}


# --- The served schema ---------------------------------------------------------


class GridFieldOption(CamelModel):
    value: str
    label_key: str


class GridField(CamelModel):
    field: str
    type: ValueType
    operators: list[str]
    sortable: bool
    label_key: str
    default_visible: bool
    options: list[GridFieldOption] | None = None


class GridSchemaRead(CamelModel):
    """KAN-461: an entity's column registry as the grid derives it — every
    field, its type, the operators it takes, whether it sorts, its label key
    and (an enum) its options."""

    fields: list[GridField]


def grid_schema(registry: Registry) -> GridSchemaRead:
    return GridSchemaRead(
        fields=[
            GridField(
                field=spec.api_name,
                type=spec.value_type,
                operators=list(spec.allowed_operators),
                sortable=spec.sortable,
                label_key=spec.label_key,
                default_visible=spec.default_visible,
                options=(
                    [GridFieldOption(value=code, label_key=f"{spec.enum_label_prefix}.{code}") for code in spec.enum_codes]
                    if spec.value_type == "enum"
                    else None
                ),
            )
            for spec in registry
        ]
    )


# --- Filters -------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Filter:
    spec: ColumnSpec
    operator: str
    value: Any


def _refuse(reason: str, message: str, **details: Any) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, "param": "filter", **details})


def _parse_decimal(raw: str, pattern: re.Pattern[str], field: str) -> decimal.Decimal:
    if not pattern.fullmatch(raw):
        raise _refuse("invalid_filter_value", f"'{raw}' is not a number for '{field}'.", field=field, value=raw)
    return decimal.Decimal(raw)


def _parse_value(spec: ColumnSpec, operator: str, raw: str | None) -> Any:
    field = spec.api_name
    if operator in _VALUELESS:
        if raw not in (None, ""):
            raise _refuse("invalid_filter_value", f"'{operator}' takes no value.", field=field)
        return None
    if raw is None or raw == "":
        raise _refuse("invalid_filter_value", f"'{field}:{operator}' needs a value.", field=field)
    kind = spec.value_type
    if kind == "text":
        if len(raw) > MAX_TEXT_FILTER_LENGTH:
            raise _refuse("invalid_filter_value", f"The value for '{field}' is too long.", field=field)
        return raw
    if kind == "enum":
        codes = raw.split(",") if operator == "in" else [raw]
        unknown = [code for code in codes if code not in spec.enum_codes]
        if unknown or not codes:
            raise _refuse(
                "invalid_filter_value", f"Unknown value for '{field}'.", field=field, value=raw,
                allowed=list(spec.enum_codes),
            )
        return codes if operator == "in" else codes[0]
    if kind in ("number", "money"):
        pattern = _MONEY if kind == "money" else _NUMBER
        if operator == "between":
            parts = raw.split(",")
            if len(parts) != 2:
                raise _refuse("invalid_filter_value", f"'between' takes two values for '{field}'.", field=field)
            low, high = (_parse_decimal(part, pattern, field) for part in parts)
            if low > high:
                raise _refuse("invalid_filter_value", f"'between' needs the lower bound first for '{field}'.", field=field)
            return (low, high)
        return _parse_decimal(raw, pattern, field)
    if kind in ("date", "datetime"):
        if operator in ("inTheLastDays", "moreThanDaysAgo"):
            if not _DAYS.fullmatch(raw) or int(raw) > MAX_FILTER_DAYS:
                raise _refuse("invalid_filter_value", f"A day count 0…{MAX_FILTER_DAYS} for '{field}'.", field=field)
            return int(raw)
        try:
            if not _ISO_DATE.fullmatch(raw):
                raise ValueError(raw)
            day = dt.date.fromisoformat(raw)
            # A day the session zone's arithmetic and the UTC bind can take
            # (9999-12-31 + 1 day, or year 1 in UTC, overflow into a 500).
            if not MIN_FILTER_YEAR <= day.year <= MAX_FILTER_YEAR:
                raise ValueError(raw)
            return day
        except ValueError:
            raise _refuse("invalid_filter_value", f"A date YYYY-MM-DD for '{field}'.", field=field, value=raw) from None
    if kind == "boolean":
        if raw not in ("true", "false"):
            raise _refuse("invalid_filter_value", f"'true' or 'false' for '{field}'.", field=field, value=raw)
        return raw == "true"
    raise AssertionError(kind)


def parse_filters(raw_filters: Sequence[str] | None, registry: Registry) -> list[Filter]:
    """Each `field:operator[:value]` — split at the first two colons, so a
    text value may hold colons of its own."""

    specs = by_name(registry)
    parsed: list[Filter] = []
    for raw in raw_filters or ():
        parts = raw.split(":", 2)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise _refuse("filter_malformed", f"Invalid filter '{raw}' — expected 'field:operator[:value]'.", value=raw)
        name, operator = parts[0], parts[1]
        value = parts[2] if len(parts) == 3 else None
        spec = specs.get(name)
        if spec is None:
            raise _refuse("unknown_field", f"Unknown filter field '{name}'.", field=name, allowed=sorted(specs))
        if operator not in spec.allowed_operators:
            raise _refuse(
                "operator_not_allowed", f"'{operator}' is not a condition of '{name}'.", field=name,
                operator=operator, allowed=list(spec.allowed_operators),
            )
        parsed.append(Filter(spec=spec, operator=operator, value=_parse_value(spec, operator, value)))
    return parsed


def _not_or_null(column: Any, clause: ColumnElement[bool]) -> ColumnElement[bool]:
    """«ist nicht» keeps the rows without a value (ADR-102): a car with no
    colour is not «red»."""

    return or_(column.is_(None), ~clause)


def _date_bounds(spec: ColumnSpec, start: dt.datetime | None, end: dt.datetime | None) -> ColumnElement[bool]:
    """[start, end) on the column — as calendar days for a date column."""

    column = spec.column
    clauses = []
    if start is not None:
        clauses.append(column >= (start.date() if spec.value_type == "date" else start))
    if end is not None:
        clauses.append(column < (end.date() if spec.value_type == "date" else end))
    return and_(true(), *clauses)


def _next_period_start(period: str, start: dt.datetime) -> dt.datetime:
    day = start.date()
    if period == "day":
        nxt = day + dt.timedelta(days=1)
    elif period == "week":
        nxt = day + dt.timedelta(days=7)
    elif period == "month":
        nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    else:
        nxt = day.replace(year=day.year + 1)
    return start_of_day(nxt)


def filter_clause(item: Filter) -> ColumnElement[bool]:
    spec, operator, value = item.spec, item.operator, item.value
    if spec.filter_builder is not None:
        return spec.filter_builder(operator, value)
    column = spec.column
    kind = spec.value_type
    if kind == "text":
        if operator == "contains":
            return column.icontains(value, autoescape=True)
        same = func.lower(column) == value.lower()
        return same if operator == "equals" else _not_or_null(column, same)
    if kind == "enum":
        enum_cls = spec.enum
        assert enum_cls is not None
        if operator == "in":
            return column.in_([enum_cls(code) for code in value])
        same = column == enum_cls(value)
        return same if operator == "is" else _not_or_null(column, same)
    if kind in ("number", "money"):
        if operator == "between":
            low, high = value
            return and_(column >= low, column <= high)
        return {
            "equals": lambda: column == value,
            "greaterThan": lambda: column > value,
            "lessThan": lambda: column < value,
            "atLeast": lambda: column >= value,
            "atMost": lambda: column <= value,
        }[operator]()
    if kind == "boolean":
        return column.is_(value)
    # date / datetime — half-open day ranges in the session time zone.
    today, _zone = today_in_session_zone()
    tomorrow = start_of_day(today + dt.timedelta(days=1))
    if operator in _PERIOD_OF:
        period = _PERIOD_OF[operator]
        start = start_of_current_period(period)  # type: ignore[arg-type]
        return _date_bounds(spec, start, _next_period_start(period, start))
    if operator == "inTheLastDays":
        return _date_bounds(spec, start_of_day_days_ago(value), tomorrow)
    if operator == "moreThanDaysAgo":
        return _date_bounds(spec, None, start_of_day_days_ago(value))
    day_start = start_of_day(value)
    next_day = start_of_day(value + dt.timedelta(days=1))
    if operator == "on":
        return _date_bounds(spec, day_start, next_day)
    if operator == "before":
        return _date_bounds(spec, None, day_start)
    return _date_bounds(spec, next_day, None)  # after


def filter_clauses(filters: Sequence[Filter]) -> list[ColumnElement[bool]]:
    return [filter_clause(item) for item in filters]


# --- Sort and cursor -------------------------------------------------------------


def _sort_column(spec: ColumnSpec) -> Any:
    return spec.sort_column if spec.sort_column is not None else spec.column


def parse_list_sort(sort: str | None, registry: Registry, *, default: Sequence[tuple[str, str]]) -> list[SortField]:
    """`field:dir[,field:dir]` over the registry; `default` (field, dir)
    pairs when the request names none. Unknown or repeated fields, a bad
    direction or a field that does not sort are 422s (U-02)."""

    specs = by_name(registry)
    pairs: list[tuple[str, str]] = []
    if sort:
        for raw_part in sort.split(","):
            part = raw_part.strip()
            if not part:
                continue
            name, sep, direction = part.partition(":")
            if not sep:
                raise UnprocessableEntityError(
                    f"Invalid sort spec '{part}' — expected 'field:asc' or 'field:desc'.",
                    details={"reason": "sort_malformed", "sort": part},
                )
            pairs.append((name, direction))
    pairs = pairs or list(default)
    fields: list[SortField] = []
    seen: set[str] = set()
    for name, direction in pairs:
        spec = specs.get(name)
        if spec is None:
            raise UnprocessableEntityError(
                f"Unknown sort field '{name}'.", details={"reason": "unknown_field", "field": name, "allowed": sorted(specs)}
            )
        if not spec.sortable:
            raise UnprocessableEntityError(
                f"'{name}' does not sort: {spec.not_sortable_reason}",
                details={"reason": "field_not_sortable", "field": name},
            )
        if direction not in ("asc", "desc"):
            raise UnprocessableEntityError(
                f"Invalid sort direction '{direction}' for field '{name}' — must be 'asc' or 'desc'.",
                details={"reason": "sort_malformed", "field": name, "direction": direction},
            )
        if name in seen:
            raise UnprocessableEntityError(
                f"Sort field '{name}' was specified more than once.", details={"reason": "sort_malformed", "field": name}
            )
        seen.add(name)
        column = _sort_column(spec)
        if spec.sort_inverted:
            direction = "desc" if direction == "asc" else "asc"
        fields.append(
            SortField(api_name=name, column=column, direction=direction, nullable=column.property.columns[0].nullable)
        )
    return fields


# --- One call ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ListQuery:
    filters: list[Filter]
    sort_fields: list[SortField]
    limit: int
    cursor: str | None


def parse_list_query(
    registry: Registry,
    *,
    filters: Sequence[str] | None,
    sort: str | None,
    default_sort: Sequence[tuple[str, str]],
    limit: int,
    cursor: str | None,
    extra_filters: Sequence[Filter] = (),
) -> ListQuery:
    """Every 4xx a list request can earn, before any query runs."""

    return ListQuery(
        filters=[*extra_filters, *parse_filters(filters, registry)],
        sort_fields=parse_list_sort(sort, registry, default=default_sort),
        limit=limit,
        cursor=cursor,
    )


def run_list_query(
    db: Session,
    stmt: Select,
    *,
    model: type,
    scope: Sequence[ColumnElement[bool]],
    query: ListQuery,
    count_threshold: int,
) -> tuple[list[Any], str | None, int, bool]:
    """The page, the next cursor, the capped count and whether it is an
    estimate. `scope` is the entity's own tenant (or group) predicates — a
    required, non-empty argument: no list runs unscoped."""

    if not scope:
        raise ValueError("a list query runs inside its scope (rule 7)")
    stmt = stmt.where(*scope, *filter_clauses(query.filters))
    total, is_estimate = count_capped(db, stmt, threshold=count_threshold)
    params = SortPageParams(
        limit=query.limit, cursor=decode_cursor_for_sort(query.cursor, query.sort_fields), sort_fields=query.sort_fields
    )
    rows = list(db.scalars(paginate_query_sorted(stmt, model=model, params=params)).all())
    page, next_cursor = build_page_for_sort(rows, params)
    return page, next_cursor, total, is_estimate
