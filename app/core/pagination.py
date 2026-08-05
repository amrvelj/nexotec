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
