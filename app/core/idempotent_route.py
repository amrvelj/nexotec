"""Idempotency-Key on every POST, as a route class (API conventions, ADR-035; KAN-119).

A router built with ``APIRouter(route_class=IdempotentRoute)`` makes every
POST route on it honour an optional ``Idempotency-Key`` header, with no code
in the handler:

- The header is declared in the route's OpenAPI (optional: requiring it is a
  breaking contract change and needs its own decision).
- After the route's own dependencies have run — authentication, permission
  checks, If-Match parsing — and its body has validated, the key is looked up
  for the caller's tenant (from the token, never the request):
  - a completed record for the same path and body: its stored response is
    returned and the handler does not run. A retried state transition gets
    its original success back, not a 409 version mismatch.
  - the same key with a different path or body: 409 (``find_cached_response``).
  - a claim still in flight: 409 — the first request is still running.
- Otherwise the key is CLAIMED before the handler runs: a row with no response
  yet, committed in its own transaction. A concurrent twin (the double click,
  the client retry racing the original) finds the claim instead of doing the
  work a second time. The hand-copied pattern this replaces stored the key
  only after the handler had committed, so two concurrent requests both
  missed the lookup and both created a record.
- A 2xx response completes the claim with the status and JSON body actually
  sent. Anything else — an exception, a non-2xx response — deletes it, so the
  user can correct the form and submit again under the same key.

What it does not cover: a process dying between the handler's commit and the
completion leaves the claim in flight. Retries then get 409, never a
duplicate, until the daily purge removes the row (``IDEMPOTENCY_RECORD_TTL``).
Closing that window entirely needs the record written inside the service's
own transaction, as ``app/inventory/services/reservation.py`` does.

Records are kept for ``IDEMPOTENCY_RECORD_TTL`` and then purged by a daily job
(``purge_expired_idempotency_records``, registered in ``app/worker.py``): a
retry comes within seconds or minutes, and a stored response can carry
personal data (a created customer's name and contact details).

The stored request is the path and the JSON body, and the replay restores the
status and JSON body only. Routes that would break either assumption are
refused when they are built (``TypeError``): a POST with query parameters or a
form/file body, and an endpoint that takes ``Response`` to set cookies or
headers. Such a route keeps a plain router until the mechanism grows to cover
it.
"""

import datetime as dt
import functools
import inspect
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, Header, Request, Response, params
from fastapi.concurrency import run_in_threadpool
from fastapi.dependencies.models import Dependant
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import ColumnElement, and_, delete, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.base import utcnow
from app.core.errors import ConflictError

# _hash_request: the claim row must carry exactly the hash find_cached_response
# compares a later request against — both from the one function.
from app.core.idempotency import _hash_request, find_cached_response
from app.core.idempotency_model import IdempotencyRecord
from app.db import get_db

logger = logging.getLogger("app.core.idempotent_route")

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_RECORD_TTL = dt.timedelta(hours=24)

# The keyword the wrapped endpoint receives the key through. It never reaches
# the original endpoint.
_KEYED_REQUEST_PARAM = "idempotency_keyed_request__"
_STATE_ATTR = "idempotency_keyed_request"


def replay_stored_response(record: IdempotencyRecord) -> Response:
    """The response a completed record stores; 409 for a claim still in flight."""

    if record.response_status is None:
        raise ConflictError(
            "A request with this Idempotency-Key is still being processed.",
            details={"idempotencyKey": record.idempotency_key},
        )
    if record.response_body is None:
        return Response(status_code=record.response_status)
    return JSONResponse(status_code=record.response_status, content=record.response_body)


class _KeyedRequest:
    """One POST carrying an Idempotency-Key: claims, completes or releases the
    key. Each step is its own short transaction on a session of its own, so
    the claim commits before the handler starts and the handler's own
    transaction never carries it."""

    def __init__(self, *, key: str, tenant_id: uuid.UUID, path: str, body: Any, bind: Engine | Connection) -> None:
        self.key = key
        self.tenant_id = tenant_id
        self.path = path
        self.body = body
        self.bind = bind
        self.claimed = False

    def _session(self) -> Session:
        return Session(bind=self.bind, autoflush=False, expire_on_commit=False)

    def _own_claim(self) -> ColumnElement[bool]:
        return and_(
            IdempotencyRecord.idempotency_key == self.key,
            IdempotencyRecord.tenant_id == self.tenant_id,
            IdempotencyRecord.response_status.is_(None),
        )

    def claim(self) -> Response | None:
        """The stored response to replay, or None once the key is claimed for
        this request. Raises 409 for a different request under the key, or
        one still in flight."""

        with self._session() as db:
            for _ in range(2):
                stored = find_cached_response(
                    db, tenant_id=self.tenant_id, key=self.key, path=self.path, body=self.body
                )
                if stored is not None:
                    return replay_stored_response(stored)
                db.add(
                    IdempotencyRecord(
                        idempotency_key=self.key,
                        tenant_id=self.tenant_id,
                        request_path=self.path,
                        request_hash=_hash_request(self.body),
                    )
                )
                try:
                    db.commit()
                except IntegrityError:
                    # A concurrent twin claimed the key between the lookup and
                    # the insert: look again, now finding its claim.
                    db.rollback()
                    continue
                self.claimed = True
                return None
        # Claimed and released by others twice in a row while we looked.
        raise ConflictError(
            "A request with this Idempotency-Key is still being processed.",
            details={"idempotencyKey": self.key},
        )

    def complete(self, response: Response) -> None:
        raw = getattr(response, "body", None)
        if not isinstance(raw, bytes) or (raw and response.media_type != "application/json"):
            # Nothing replayable to store: let a retry run the route again.
            logger.warning(
                "idempotent POST answered with a body that cannot be stored; claim released",
                extra={"path": self.path, "mediaType": response.media_type},
            )
            self.release()
            return
        with self._session() as db:
            db.execute(
                update(IdempotencyRecord)
                .where(self._own_claim())
                .values(response_status=response.status_code, response_body=json.loads(raw) if raw else None)
            )
            db.commit()

    def release(self) -> None:
        with self._session() as db:
            db.execute(delete(IdempotencyRecord).where(self._own_claim()))
            db.commit()


async def _keyed_request(
    request: Request,
    idempotency_key: str | None = Header(
        default=None,
        alias=IDEMPOTENCY_KEY_HEADER,
        max_length=255,
        description=(
            "Optional. A fresh value (a UUID) per form submission, reused only when retrying that same "
            "submission. A retry gets the original response; the same key with a different body is a 409."
        ),
    ),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> _KeyedRequest | None:
    if not idempotency_key:
        return None
    raw = await request.body()
    body: Any
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        # A route without a body parameter never parses what was sent; hash it as text.
        body = raw.decode("utf-8", "replace")
    keyed = _KeyedRequest(
        key=idempotency_key, tenant_id=principal.tenant_id, path=request.url.path, body=body, bind=db.get_bind()
    )
    setattr(request.state, _STATE_ATTR, keyed)
    return keyed


def _with_idempotency_key(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """The endpoint plus one trailing dependency, `_keyed_request`, resolved
    after every dependency the endpoint declares — so authentication and
    permission checks run before a stored response is ever replayed."""

    try:
        signature = inspect.signature(endpoint, eval_str=True)
    except NameError:
        signature = inspect.signature(endpoint)
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in signature.parameters.values()):
        raise TypeError(f"IdempotentRoute cannot wrap {endpoint.__qualname__}: it takes *args or **kwargs")
    keyed_param = inspect.Parameter(
        _KEYED_REQUEST_PARAM, inspect.Parameter.KEYWORD_ONLY, default=Depends(_keyed_request), annotation=Any
    )

    wrapper: Callable[..., Any]
    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            keyed: _KeyedRequest | None = kwargs.pop(_KEYED_REQUEST_PARAM)
            if keyed is not None:
                stored = await run_in_threadpool(keyed.claim)
                if stored is not None:
                    return stored
            return await endpoint(*args, **kwargs)

        wrapper = async_wrapper
    else:

        @functools.wraps(endpoint)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            keyed: _KeyedRequest | None = kwargs.pop(_KEYED_REQUEST_PARAM)
            if keyed is not None:
                stored = keyed.claim()
                if stored is not None:
                    return stored
            return endpoint(*args, **kwargs)

        wrapper = sync_wrapper

    wrapper.__signature__ = signature.replace(  # type: ignore[union-attr]
        parameters=[*signature.parameters.values(), keyed_param]
    )
    return wrapper


def _dependants(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _dependants(sub)


def _refuse_unsupported(route: APIRoute) -> None:
    where = f"POST {route.path} ({route.endpoint.__qualname__})"
    for dependant in _dependants(route.dependant):
        if dependant.query_params:
            raise TypeError(f"{where}: query parameters are not part of the stored request")
        if any(isinstance(field.field_info, (params.Form, params.File)) for field in dependant.body_params):
            raise TypeError(f"{where}: a form or file body is not part of the stored request")
        if dependant.response_param_name is not None:
            raise TypeError(f"{where}: headers or cookies set on Response are not replayed")


class IdempotentRoute(APIRoute):
    """Every POST on a router built with this class honours Idempotency-Key
    (module docstring). Other methods are untouched."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        self.honours_idempotency_key = "POST" in {m.upper() for m in kwargs.get("methods") or ()}
        if self.honours_idempotency_key:
            endpoint = _with_idempotency_key(endpoint)
        super().__init__(path, endpoint, **kwargs)
        if self.honours_idempotency_key:
            _refuse_unsupported(self)

    def get_route_handler(self) -> Callable[[Request], Any]:
        handler = super().get_route_handler()
        if not self.honours_idempotency_key:
            return handler

        async def settle_idempotency_key(request: Request) -> Response:
            try:
                response: Response = await handler(request)
            except Exception:
                keyed = getattr(request.state, _STATE_ATTR, None)
                if keyed is not None and keyed.claimed:
                    await _settle(keyed.release)
                raise
            keyed = getattr(request.state, _STATE_ATTR, None)
            if keyed is not None and keyed.claimed:
                if 200 <= response.status_code < 300:
                    await _settle(keyed.complete, response)
                else:
                    await _settle(keyed.release)
            return response

        return settle_idempotency_key


async def _settle(step: Callable[..., None], *args: Any) -> None:
    """Completing or releasing a claim never changes the answer the caller
    gets: the route has already done its work (or failed). A step that fails
    leaves the claim in flight — retries get 409, never a duplicate — until
    the daily purge."""

    try:
        await run_in_threadpool(step, *args)
    except Exception:
        logger.exception("could not settle an Idempotency-Key claim; it stays in flight until purged")


def purge_expired_idempotency_records(db: Session, *, now: dt.datetime | None = None) -> int:
    """Daily job: deletes every record older than IDEMPOTENCY_RECORD_TTL,
    across tenants, in-flight claims included. Returns the number deleted."""

    cutoff = (now or utcnow()) - IDEMPOTENCY_RECORD_TTL
    result = db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at <= cutoff))
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def run_daily_idempotency_record_purge(db: Session) -> None:
    deleted = purge_expired_idempotency_records(db)
    if deleted:
        logger.info("idempotency record purge", extra={"deleted": deleted})
