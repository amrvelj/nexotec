"""POST idempotency records (API conventions, ADR-035).

An HTTP route does not call this module. Its router is built with
``route_class=IdempotentRoute`` (``app/core/idempotent_route.py``), which
claims, replays and completes the record for every POST; the guard
``tests/architecture/test_every_post_accepts_idempotency_key.py`` refuses a
POST without it.

A context calls ``find_cached_response`` and ``store_response`` directly only
for a keyed write of its own, inside its own transaction and under a path of
its own (``inventory.reserve:<item>`` in
``app/inventory/services/reservation.py``): a repeat of the request gets the
stored response, the same key with another request a 409. The daily purge of
HTTP records leaves these alone.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.idempotency_model import IdempotencyRecord


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
