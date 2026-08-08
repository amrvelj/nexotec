"""Opaque cursor pagination shared by every list endpoint.

Cursor encodes the last-seen (created_at, id) pair as base64 JSON. Callers
must treat it as opaque — it's not a page number and isn't guaranteed
stable across schema changes.
"""

import base64
import dataclasses
import datetime as dt
import json
import uuid
from typing import Any, TypeVar

from fastapi import Query
from sqlalchemy import Select, and_, or_

from app.core.config import get_settings
from app.core.errors import BadRequestError

settings = get_settings()

ModelT = TypeVar("ModelT")


@dataclasses.dataclass(frozen=True)
class CursorPosition:
    created_at: dt.datetime
    id: uuid.UUID


def encode_cursor(position: CursorPosition) -> str:
    payload = {"created_at": position.created_at.isoformat(), "id": str(position.id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> CursorPosition:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
        return CursorPosition(
            created_at=dt.datetime.fromisoformat(payload["created_at"]),
            id=uuid.UUID(payload["id"]),
        )
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise BadRequestError("Cursor is malformed or invalid.") from exc


@dataclasses.dataclass(frozen=True)
class PageParams:
    limit: int
    cursor: CursorPosition | None


def page_params(
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
) -> PageParams:
    return PageParams(limit=limit, cursor=decode_cursor(cursor) if cursor else None)


def paginate_query(stmt: Select, *, model: type[ModelT], params: PageParams) -> Select:
    """Apply the shared (created_at, id) keyset-cursor ordering/filtering to
    a list-endpoint query. Fetches one extra row (limit + 1) so the caller
    can detect whether a next page exists — see build_page.
    """

    if params.cursor is not None:
        stmt = stmt.where(
            or_(
                model.created_at > params.cursor.created_at,  # type: ignore[attr-defined]
                and_(
                    model.created_at == params.cursor.created_at,  # type: ignore[attr-defined]
                    model.id > params.cursor.id,  # type: ignore[attr-defined]
                ),
            )
        )
    return stmt.order_by(model.created_at.asc(), model.id.asc()).limit(params.limit + 1)  # type: ignore[attr-defined]


def build_page(rows: list[Any], params: PageParams) -> tuple[list[Any], str | None]:
    """Split the over-fetched rows into the page to return plus an opaque
    next_cursor (None when there's no further page).
    """

    has_more = len(rows) > params.limit
    page_rows = rows[: params.limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(CursorPosition(created_at=last.created_at, id=last.id))
    return page_rows, next_cursor


# --- Dynamic multi-column sort (U-02) ---------------------------------------
#
# Deliberately parallel to, not a generalization of, everything above: the
# fixed (created_at, id) cursor path stays exactly as it was, still used
# unchanged by every list endpoint that hasn't adopted `?sort=` yet
# (vehicles, reference-data, transactions). Generalizing paginate_query in
# place to a dynamic column list — with per-column direction and the
# nulls-always-last rule FR-UI-01 requires — is real, easy-to-get-subtly-
# wrong logic; better to prove it here, on one endpoint, than risk every
# existing list at once. A later cleanup can fold call sites over once this
# path is established.


def _serialize_sort_value(value: Any) -> list:
    if value is None:
        return ["null", None]
    if isinstance(value, dt.datetime):
        return ["dt", value.isoformat()]
    if isinstance(value, uuid.UUID):
        return ["uuid", str(value)]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, str):
        return ["str", value]
    raise TypeError(f"Unsupported cursor value type for sorted pagination: {type(value)!r}")


def _deserialize_sort_value(tagged: Any) -> Any:
    tag, value = tagged
    if tag == "null":
        return None
    if tag == "dt":
        return dt.datetime.fromisoformat(value)
    if tag == "uuid":
        return uuid.UUID(value)
    if tag in ("bool", "int", "str"):
        return value
    raise ValueError(f"Unknown cursor value tag: {tag!r}")


@dataclasses.dataclass(frozen=True)
class SortCursorPosition:
    """One value per active sort field (same order as the request's
    sort_fields), plus the row id as the final, always-unique tiebreaker.
    """

    values: tuple[Any, ...]
    id: uuid.UUID


def encode_sort_cursor(position: SortCursorPosition) -> str:
    payload = [_serialize_sort_value(v) for v in position.values] + [["uuid", str(position.id)]]
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_sort_cursor(cursor: str) -> SortCursorPosition:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) < 1:
            raise ValueError("Cursor payload is empty.")
        *value_items, id_item = payload
        values = tuple(_deserialize_sort_value(v) for v in value_items)
        row_id = _deserialize_sort_value(id_item)
        return SortCursorPosition(values=values, id=row_id)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BadRequestError("Cursor is malformed or invalid.") from exc


@dataclasses.dataclass(frozen=True)
class SortPageParams:
    limit: int
    cursor: SortCursorPosition | None
    sort_fields: list  # list[app.core.sorting.SortField]


def paginate_query_sorted(stmt: Select, *, model: type[ModelT], params: SortPageParams) -> Select:
    """Like paginate_query, but ordered by a caller-supplied, dynamic list
    of (column, direction) pairs instead of the fixed (created_at, id).

    Nulls always sort last, in both directions (FR-UI-01) — the one piece
    of real complexity here: a naive `column > value` keyset predicate
    silently drops NULL rows from ever matching, because SQL's three-valued
    NULL comparison means neither `>`, `<`, nor `=` is ever true against
    NULL. Handled per sort field: if the cursor's value for that field is
    itself NULL, "still after this position" means "also NULL" (the cursor
    row was already in the null tail, and the tail is only ordered by
    whatever comes next); if the cursor's value is not NULL, "after" means
    the usual `>`/`<` for the requested direction OR NULL (since NULL is
    always further along than any real value, regardless of direction).

    `id` is always appended as the final tiebreaker column (ascending,
    never NULL) so pagination stays deterministic even when every
    requested sort field ties across multiple rows.
    """

    sort_fields = params.sort_fields

    order_by = []
    for field in sort_fields:
        column_order = field.column.desc() if field.direction == "desc" else field.column.asc()
        order_by.append(column_order.nulls_last())
    order_by.append(model.id.asc())  # type: ignore[attr-defined]
    stmt = stmt.order_by(*order_by)

    if params.cursor is not None:
        clauses = []
        equal_so_far = []
        for field, cursor_value in zip(sort_fields, params.cursor.values):
            if cursor_value is None:
                # There's no "more null than null" — ties among NULLs can
                # only be broken by the next field (or the final id
                # tiebreak), so a NULL cursor value contributes no
                # standalone "strictly after" clause at this level. Without
                # this branch, `column IS NULL` would wrongly re-match every
                # already-returned null row too, and pagination would never
                # advance past the null tail.
                equal_so_far.append(field.column.is_(None))
                continue
            if field.direction == "desc":
                strictly_after = or_(field.column.is_(None), field.column < cursor_value)
            else:
                strictly_after = or_(field.column.is_(None), field.column > cursor_value)
            clauses.append(and_(*equal_so_far, strictly_after))
            equal_so_far.append(field.column == cursor_value)
        clauses.append(and_(*equal_so_far, model.id > params.cursor.id))  # type: ignore[attr-defined]
        stmt = stmt.where(or_(*clauses))

    return stmt.limit(params.limit + 1)


def build_sorted_page(rows: list[Any], params: SortPageParams) -> tuple[list[Any], str | None]:
    has_more = len(rows) > params.limit
    page_rows = rows[: params.limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        values = tuple(getattr(last, field.column.key) for field in params.sort_fields)
        next_cursor = encode_sort_cursor(SortCursorPosition(values=values, id=last.id))
    return page_rows, next_cursor
