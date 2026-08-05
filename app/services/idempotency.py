"""POST idempotency-key handling (API-conventions cross-cutting rule).

Usage in a route handler:

    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")

    def create_widget(body, request, idempotency_key=..., principal=..., db=...):
        if idempotency_key:
            cached = find_cached_response(db, tenant_id=principal.tenant_id,
                                           key=idempotency_key, path=request.url.path,
                                           body=body.model_dump(mode="json"))
            if cached is not None:
                return JSONResponse(status_code=cached.response_status, content=cached.response_body)
        ... create the widget ...
        if idempotency_key:
            store_response(db, tenant_id=..., key=..., path=..., body=...,
                            response_status=201, response_body=result)
        return result
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.idempotency import IdempotencyRecord


def _hash_request(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_cached_response(
    db: Session, *, tenant_id: uuid.UUID, key: str, path: str, body: Any
) -> IdempotencyRecord | None:
    request_hash = _hash_request(body)
    existing = db.get(IdempotencyRecord, (key, tenant_id))
    if existing is None:
        return None
    if existing.request_path != path or existing.request_hash != request_hash:
        raise ConflictError(
            "Idempotency-Key was already used with a different request.",
            details={"idempotencyKey": key},
        )
    return existing


def store_response(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    key: str,
    path: str,
    body: Any,
    response_status: int,
    response_body: dict[str, Any],
) -> IdempotencyRecord:
    record = IdempotencyRecord(
        idempotency_key=key,
        tenant_id=tenant_id,
        request_path=path,
        request_hash=_hash_request(body),
        response_status=response_status,
        response_body=response_body,
    )
    db.add(record)
    db.flush()
    return record
