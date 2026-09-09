import os

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Settings.tax_id_encryption_key and Settings.jwt_private_key are both
# required with no default (see app/core/config.py — an earlier draft
# hardcoded a live tax_id key here and it leaked into git history). Generate
# fresh values per test run rather than checking static ones into the repo;
# neither protects anything real, only test fixtures. Must be set before any
# app.* import below, since get_settings() runs at import time in app/db.py,
# app/core/auth.py, app/core/pagination.py.
os.environ.setdefault("DMS_TAX_ID_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault(
    "DMS_JWT_PRIVATE_KEY",
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode("ascii"),
)
# Zitadel OIDC settings (WP-4) — also required with no default. Never
# actually dialed: app.platform.services.oidc.get_oidc_client is overridden
# below (see the `client` fixture) with a fake that never makes a network
# call, so these values only need to be syntactically present for
# Settings()/authlib's OAuth.register() to construct without error.
os.environ.setdefault("DMS_ZITADEL_ISSUER", "https://example.zitadel.cloud")
os.environ.setdefault("DMS_ZITADEL_CLIENT_ID", "test-client-id")
os.environ.setdefault("DMS_ZITADEL_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DMS_ZITADEL_REDIRECT_URI", "http://testserver/v1/auth/oidc/callback")
os.environ.setdefault("DMS_SESSION_SECRET_KEY", Fernet.generate_key().decode())

import app.model_registry  # noqa: F401  ensures all tables are registered on Base.metadata
from app.db import Base, get_db
from app.main import app as fastapi_app
from app.platform.services.oidc import get_oidc_client
from tests.demo_models import DemoWidget  # noqa: F401  registers the test-only tenant-scoped model
from tests.fake_oidc import FakeOidcClient

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


@pytest.fixture(autouse=True)
def _legacy_vehicle_writes_open():
    """WP-5 PR-3 flipped `legacy_vehicle_write_frozen` to True in
    production. Most of the suite predates the three-layer model and uses
    the legacy `POST /v1/vehicles` endpoint as a plain fixture surface —
    keep it open by default so those tests still build their fixtures.
    `tests/test_vehicle_legacy_freeze.py` clears the settings cache and
    drives the flag itself, so it is unaffected by this.
    """

    from app.core.config import get_settings

    settings = get_settings()
    original = settings.legacy_vehicle_write_frozen
    settings.legacy_vehicle_write_frozen = False
    try:
        yield
    finally:
        settings.legacy_vehicle_write_frozen = original


@pytest.fixture()
def engine():
    eng = _make_engine()
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(autouse=True)
def _seed_country_reference_list(engine):
    """Stand up the ``country`` ReferenceList in every test DB (KAN-32).

    Autouse, which is a departure from the house pattern of per-test
    ``_seed_list`` / ``_seed_value`` helpers. It is justified here:
    ``CustomerAddressCreate.address_country`` defaults to ``"CH"``, so *every*
    test that creates a customer-with-address or writes an address now runs
    ``_validate_country_codes`` — and with the list absent that raises a
    500-class deployment fault (``get_active_reference_value_codes`` -> None),
    not a skippable 404. Seeding it once here keeps the whole suite honest
    without threading a fixture through hundreds of call sites.

    ``tests/test_country_reference_list.py`` keeps one test that runs with
    the list dropped, pinning the deployment-fault behaviour.

    The rows are the real seed data (``load_country_seed()``), the same
    source the Alembic migration uses.
    """

    from sqlalchemy import insert

    from app.core.base import utcnow
    from app.core.uuid7 import uuid7
    from app.platform.models.reference_data import ReferenceList, ReferenceValue
    from app.platform.reference_data_seed import load_country_seed

    now = utcnow()
    rows = load_country_seed()
    with engine.begin() as conn:
        list_id = uuid7()
        conn.execute(
            insert(ReferenceList.__table__).values(
                id=list_id, list_code="country", created_at=now, updated_at=now
            )
        )
        conn.execute(
            insert(ReferenceValue.__table__),
            [
                {
                    "id": uuid7(),
                    "list_id": list_id,
                    "value_code": code,
                    "label_de": de,
                    "label_fr": fr,
                    "label_it": it,
                    "label_en": en,
                    "sort_order": order,
                    "active": True,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for order, (code, de, fr, it, en) in enumerate(rows)
            ],
        )
    yield


@pytest.fixture()
def db_session(engine) -> Session:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def oidc_fake() -> FakeOidcClient:
    """The OIDC test double behind the `client` fixture's dependency
    override below — a test that needs to script a login outcome depends
    on both fixtures and calls oidc_fake.enqueue_identity(...)/
    enqueue_error(...) before hitting GET /v1/auth/oidc/callback.
    """

    return FakeOidcClient()


@pytest.fixture()
def client(engine, oidc_fake) -> TestClient:
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    fastapi_app.dependency_overrides[get_oidc_client] = lambda: oidc_fake
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(get_oidc_client, None)
