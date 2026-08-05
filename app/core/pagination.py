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

from fastapi import Query

from app.core.config import get_settings
from app.core.errors import BadRequestError

settings = get_settings()


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
