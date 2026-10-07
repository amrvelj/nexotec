"""The Idempotency-Key route class itself (app/core/idempotent_route.py, KAN-119).

Exercised on a small app of its own, so each property is pinned on a route
built for it: replay, 409 on reuse with another request, the claim against a
concurrent twin, release on failure, authorisation before replay, a retried
transition, async endpoints, the routes the class refuses, and the purge.
The per-route tests for the real routes live beside each context's tests
(tests/test_platform_idempotency.py, ...).
"""

import datetime as dt
import os
import threading
import uuid

import pytest
from fastapi import APIRouter, Depends, FastAPI, Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core import idempotent_route
from app.core.auth import Principal, create_access_token, get_current_principal
from app.core.base import utcnow
from app.core.concurrency import check_version, require_if_match
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.core.errors import ConflictError, ForbiddenError, register_error_handlers
from app.core.idempotency_model import IdempotencyRecord
from app.core.idempotent_route import (
    IDEMPOTENCY_RECORD_TTL,
    IdempotentRoute,
    _KeyedRequest,
    purge_expired_idempotency_records,
    replay_stored_response,
)
from app.db import get_db

_postgres_only = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="concurrent sessions on one shared SQLite connection prove nothing; Postgres is the lane of record",
)


class Thing(BaseModel):
    name: str


class _Backend:
    """What the test app's routes did, so a test can count the work."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.version = 1
        self.fail_next = False
        self.started = threading.Event()
        self.proceed = threading.Event()


def _build_app(backend: _Backend, engine) -> FastAPI:
    router = APIRouter(route_class=IdempotentRoute)

    @router.post("/things", status_code=201)
    def create_thing(body: Thing, principal: Principal = Depends(get_current_principal)):
        if backend.fail_next:
            backend.fail_next = False
            raise ConflictError("Refused once.")
        backend.calls.append(body.name)
        return {"id": str(uuid.uuid4()), "name": body.name}

    @router.post("/other-things", status_code=201)
    def create_other_thing(body: Thing, principal: Principal = Depends(get_current_principal)):
        backend.calls.append(f"other:{body.name}")
        return {"id": str(uuid.uuid4()), "name": body.name}

    @router.post("/things/{thing_id}/finish")
    def finish_thing(
        thing_id: uuid.UUID,
        if_match: int = Depends(require_if_match),
        principal: Principal = Depends(get_current_principal),
    ):
        check_version(backend.version, if_match, entity_name="Thing")
        backend.version += 1
        backend.calls.append("finish")
        return {"id": str(thing_id), "version": backend.version}

    @router.post("/slow-things", status_code=201)
    def create_slow_thing(body: Thing, principal: Principal = Depends(get_current_principal)):
        backend.calls.append(body.name)
        backend.started.set()
        assert backend.proceed.wait(10)
        return {"id": str(uuid.uuid4()), "name": body.name}

    def _managers_only(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.is_dealer_manager:
            raise ForbiddenError("Managers only.")
        return principal

    @router.post("/guarded-things", status_code=201)
    def create_guarded_thing(body: Thing, principal: Principal = Depends(_managers_only)):
        backend.calls.append(body.name)
        return {"id": str(uuid.uuid4()), "name": body.name}

    @router.post("/async-things", status_code=201)
    async def create_async_thing(body: Thing, principal: Principal = Depends(get_current_principal)):
        backend.calls.append(body.name)
        return {"id": str(uuid.uuid4()), "name": body.name}

    @router.post("/declined-things")
    def decline_thing(body: Thing, principal: Principal = Depends(get_current_principal)) -> JSONResponse:
        backend.calls.append(body.name)
        return JSONResponse(status_code=503, content={"error": {"code": "busy", "message": "Busy."}})

    @router.get("/things")
    def list_things(principal: Principal = Depends(get_current_principal)):
        return backend.calls

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/v1")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    return app


@pytest.fixture()
def backend() -> _Backend:
    return _Backend()


@pytest.fixture()
def test_client(backend, engine) -> TestClient:
    return TestClient(_build_app(backend, engine))


def _headers(*, tenant_id: uuid.UUID | None = None, key: str | None = None, manager: bool = True, **extra) -> dict:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or _TENANT,
        group_id=uuid.uuid4(),
        roles=frozenset(),
        is_dealer_manager=manager,
    )
    headers = {"Authorization": f"Bearer {token}", **extra}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


_TENANT = uuid.uuid4()


def _records(db_session) -> list[IdempotencyRecord]:
    db_session.expire_all()
    return list(db_session.scalars(select(IdempotencyRecord)))


# --- replay and reuse -----------------------------------------------------


def test_the_same_key_twice_runs_the_route_once_and_answers_the_same(test_client, backend, db_session):
    key = str(uuid.uuid4())
    first = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key))
    second = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key))

    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert second.json() == first.json()
    assert backend.calls == ["a"]
    [record] = _records(db_session)
    assert (record.response_status, record.response_body) == (201, first.json())


def test_the_same_key_with_a_different_body_is_a_409(test_client, backend):
    key = str(uuid.uuid4())
    assert test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key)).status_code == 201

    response = test_client.post("/v1/things", json={"name": "b"}, headers=_headers(key=key))

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"idempotencyKey": key}
    assert backend.calls == ["a"]


def test_the_same_key_on_another_route_is_a_409(test_client, backend):
    key = str(uuid.uuid4())
    assert test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key)).status_code == 201

    response = test_client.post("/v1/other-things", json={"name": "a"}, headers=_headers(key=key))

    assert response.status_code == 409
    assert backend.calls == ["a"]


def test_without_a_key_every_request_runs_and_nothing_is_stored(test_client, backend, db_session):
    for _ in range(2):
        assert test_client.post("/v1/things", json={"name": "a"}, headers=_headers()).status_code == 201

    assert backend.calls == ["a", "a"]
    assert _records(db_session) == []


def test_keys_are_scoped_to_the_tenant_from_the_token(test_client, backend):
    key = str(uuid.uuid4())
    for tenant_id in (uuid.uuid4(), uuid.uuid4()):
        response = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(tenant_id=tenant_id, key=key))
        assert response.status_code == 201

    assert backend.calls == ["a", "a"]


def test_body_key_order_and_spacing_do_not_make_another_request(test_client, backend):
    key = str(uuid.uuid4())
    headers = _headers(key=key, **{"Content-Type": "application/json"})
    first = test_client.post("/v1/things", content=b'{"name": "a"}', headers=headers)
    second = test_client.post("/v1/things", content=b'{"name":"a"}', headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert backend.calls == ["a"]


# --- the claim against concurrent twins -----------------------------------


@_postgres_only
def test_a_concurrent_twin_gets_409_and_never_runs_the_route(test_client, backend, db_session):
    """The double click: the second request arrives while the first is still
    running. Before KAN-119 both missed the lookup and both did the work."""

    key = str(uuid.uuid4())
    first: list = []
    thread = threading.Thread(
        target=lambda: first.append(test_client.post("/v1/slow-things", json={"name": "a"}, headers=_headers(key=key)))
    )
    thread.start()
    try:
        assert backend.started.wait(10)
        [claim] = _records(db_session)
        assert claim.response_status is None

        twin = test_client.post("/v1/slow-things", json={"name": "a"}, headers=_headers(key=key))
    finally:
        backend.proceed.set()
        thread.join(10)

    assert twin.status_code == 409
    assert "still being processed" in twin.json()["error"]["message"]
    assert first[0].status_code == 201
    retry = test_client.post("/v1/slow-things", json={"name": "a"}, headers=_headers(key=key))
    assert retry.json() == first[0].json()
    assert backend.calls == ["a"]


@_postgres_only
def test_a_twin_that_loses_the_insert_race_finds_the_claim(engine, db_session, monkeypatch):
    """Both looked before either inserted: the loser's insert hits the primary
    key and it looks again, now finding the winner's claim."""

    def keyed() -> _KeyedRequest:
        return _KeyedRequest(key="k", tenant_id=_TENANT, path="/v1/things", body={"name": "a"}, bind=engine)

    winner = keyed()
    assert winner.claim() is None

    real_lookup = idempotent_route.find_cached_response
    lookups: list[int] = []

    def lookup_missing_the_winner_once(*args, **kwargs):
        lookups.append(1)
        return None if len(lookups) == 1 else real_lookup(*args, **kwargs)

    monkeypatch.setattr(idempotent_route, "find_cached_response", lookup_missing_the_winner_once)
    loser = keyed()
    with pytest.raises(ConflictError, match="still being processed"):
        loser.claim()

    assert len(lookups) == 2
    assert not loser.claimed
    assert len(_records(db_session)) == 1


# --- release on failure ---------------------------------------------------


def test_a_failed_request_releases_the_key_for_a_corrected_retry(test_client, backend, db_session):
    key = str(uuid.uuid4())
    backend.fail_next = True
    refused = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key))
    assert refused.status_code == 409
    assert _records(db_session) == []

    retried = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key))
    assert retried.status_code == 201
    assert backend.calls == ["a"]


def test_an_invalid_body_never_claims_the_key(test_client, backend, db_session):
    key = str(uuid.uuid4())
    response = test_client.post("/v1/things", json={"nom": "a"}, headers=_headers(key=key))

    assert response.status_code == 422
    assert _records(db_session) == []
    assert test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key=key)).status_code == 201


def test_a_non_2xx_response_is_not_stored(test_client, backend, db_session):
    key = str(uuid.uuid4())
    for _ in range(2):
        response = test_client.post("/v1/declined-things", json={"name": "a"}, headers=_headers(key=key))
        assert response.status_code == 503

    assert backend.calls == ["a", "a"]
    assert _records(db_session) == []


# --- authorisation, transitions, async ------------------------------------


def test_a_replay_still_requires_the_routes_own_authorisation(test_client, backend):
    key = str(uuid.uuid4())
    created = test_client.post("/v1/guarded-things", json={"name": "a"}, headers=_headers(key=key, manager=True))
    assert created.status_code == 201

    replay_attempt = test_client.post(
        "/v1/guarded-things", json={"name": "a"}, headers=_headers(key=key, manager=False)
    )

    assert replay_attempt.status_code == 403
    assert backend.calls == ["a"]


def test_a_retried_transition_gets_its_original_success_not_a_version_conflict(test_client, backend):
    thing_id = uuid.uuid4()
    key = str(uuid.uuid4())
    first = test_client.post(f"/v1/things/{thing_id}/finish", headers=_headers(key=key, **{"If-Match": "1"}))
    retried = test_client.post(f"/v1/things/{thing_id}/finish", headers=_headers(key=key, **{"If-Match": "1"}))
    unkeyed = test_client.post(f"/v1/things/{thing_id}/finish", headers=_headers(**{"If-Match": "1"}))

    assert first.status_code == 200, first.text
    assert retried.status_code == 200
    assert retried.json() == first.json() == {"id": str(thing_id), "version": 2}
    assert unkeyed.status_code == 409
    assert backend.calls == ["finish"]


def test_an_async_route_honours_the_key_too(test_client, backend):
    key = str(uuid.uuid4())
    first = test_client.post("/v1/async-things", json={"name": "a"}, headers=_headers(key=key))
    second = test_client.post("/v1/async-things", json={"name": "a"}, headers=_headers(key=key))

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert backend.calls == ["a"]


# --- the contract ---------------------------------------------------------


def test_the_header_is_declared_optional_and_bounded_on_posts_only(backend, engine):
    spec = _build_app(backend, engine).openapi()

    [header] = [
        p for p in spec["paths"]["/v1/things"]["post"]["parameters"] if p["name"] == "Idempotency-Key"
    ]
    assert header["in"] == "header"
    assert header["required"] is False
    assert header["schema"]["anyOf"][0]["maxLength"] == 255
    assert all(p["name"] != "Idempotency-Key" for p in spec["paths"]["/v1/things"]["get"].get("parameters", []))


def test_a_key_longer_than_the_column_is_a_422(test_client, backend):
    response = test_client.post("/v1/things", json={"name": "a"}, headers=_headers(key="k" * 256))

    assert response.status_code == 422
    assert backend.calls == []


def test_routes_a_replay_cannot_reproduce_are_refused_when_built():
    router = APIRouter(route_class=IdempotentRoute)

    with pytest.raises(TypeError, match="query parameters"):

        @router.post("/with-query")
        def with_query(dry_run: bool = False):
            return {}

    with pytest.raises(TypeError, match="Response"):

        @router.post("/with-cookie")
        def with_cookie(response: Response):
            response.set_cookie("session", "x")
            return {}

    @router.get("/reads-may-take-queries")
    def read_with_query(dry_run: bool = False):
        return {}


def test_a_claim_in_flight_has_nothing_to_replay():
    record = IdempotencyRecord(idempotency_key="k", tenant_id=_TENANT, request_path="/p", request_hash="h")

    with pytest.raises(ConflictError, match="still being processed"):
        replay_stored_response(record)

    record.response_status, record.response_body = 204, None
    assert replay_stored_response(record).status_code == 204


# --- retention ------------------------------------------------------------


def test_records_older_than_a_day_are_purged_in_flight_claims_included(db_session):
    now = utcnow()

    def record(key: str, age: dt.timedelta, status: int | None) -> IdempotencyRecord:
        return IdempotencyRecord(
            idempotency_key=key,
            tenant_id=_TENANT,
            request_path="/v1/things",
            request_hash="h",
            response_status=status,
            response_body={"id": key} if status else None,
            created_at=now - age,
        )

    db_session.add_all(
        [
            record("expired", dt.timedelta(hours=25), 201),
            record("stuck", dt.timedelta(hours=25), None),
            record("fresh", dt.timedelta(hours=23), 201),
        ]
    )
    db_session.commit()

    assert purge_expired_idempotency_records(db_session, now=now) == 2
    assert [r.idempotency_key for r in _records(db_session)] == ["fresh"]
    assert IDEMPOTENCY_RECORD_TTL == dt.timedelta(hours=24)
    assert db_session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_the_worker_registers_the_daily_purge():
    _REGISTRY.clear()
    try:
        worker.register_daily_jobs()
        assert "core.idempotency_records.purge" in registered_job_names()
    finally:
        _REGISTRY.clear()
