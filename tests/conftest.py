import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Settings.tax_id_encryption_key is required with no default (see
# app/core/config.py — a previous default leaked a live key into git
# history). Generate a fresh key per test run rather than checking a
# static value into the repo; it never protects real data, only test
# fixtures. Must be set before any app.* import below, since get_settings()
# runs at import time in app/db.py, app/core/auth.py, app/core/pagination.py.
os.environ.setdefault("DMS_TAX_ID_ENCRYPTION_KEY", Fernet.generate_key().decode())

import app.model_registry  # noqa: F401  ensures all tables are registered on Base.metadata
from app.db import Base, get_db
from app.main import app as fastapi_app
from tests.demo_models import DemoWidget  # noqa: F401  registers the test-only tenant-scoped model

# Two test lanes (CTO condition on issue #2's merge): fast SQLite in-memory
# by default, or the real Postgres container from docker-compose when
# DMS_TEST_DATABASE_URL is set — see README "Running tests" and
# .github/workflows/test.yml. User is the first FK relationship in the
# schema; SQLite's weaker constraint/concurrency enforcement can hide bugs
# that only show up against Postgres, so both lanes run in CI.
_TEST_DATABASE_URL = os.environ.get("DMS_TEST_DATABASE_URL")


def _make_engine():
    if _TEST_DATABASE_URL:
        return create_engine(_TEST_DATABASE_URL, pool_pre_ping=True)
    # StaticPool: TestClient runs the app in a separate thread (anyio
    # portal), and plain sqlite+pysqlite:///:memory: hands out a fresh
    # (empty) in-memory database per connection/thread otherwise — this
    # keeps every checkout on the single shared connection regardless of
    # thread, needed once `client` below drives real HTTP requests through
    # entity routers that hit the DB (issue #2+).
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture()
def engine():
    eng = _make_engine()
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(engine) -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine) -> TestClient:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
