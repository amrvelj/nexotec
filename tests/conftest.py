import os

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
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
# that only show up against Postgres, so CI runs the Postgres lane only
# (ADR-011).
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


# The Postgres lane builds the schema once per session, not once per test.
# create_all + drop_all of every table and index around every test cost about
# a second per test on Postgres, most of the lane's CI time, none of it
# testing anything. Each test still starts where create_all left it: the
# schema exactly as Base.metadata describes it, every table empty in new
# storage (TRUNCATE, not DELETE: no dead rows pile up over the session, and
# the table is what a newly created one is), identity sequences at their
# start. _PostgresSchema.reset() re-establishes that before every test, and
# rebuilds the schema if a test changed it (none does today; one that does
# still gets what create_all gave it). The SQLite fast lane keeps a fresh
# in-memory database per test.

# Everything a test can observe about the schema of Base.metadata's tables:
# the tables, their columns, defaults, constraints, indexes and triggers.
# Rows and storage are not part of it, so TRUNCATE leaves it unchanged; any
# DDL on these tables changes it.
_SCHEMA_SIGNATURE = text(
    """
    WITH t AS (
        SELECT oid, relname, relpersistence, relrowsecurity, reloptions, relacl FROM pg_class
        WHERE relnamespace = current_schema()::regnamespace AND relkind = 'r' AND relname = ANY(:names)
    )
    SELECT md5(string_agg(line, E'\\n' ORDER BY line)) FROM (
        SELECT concat_ws(' ', 'table', relname, relpersistence, relrowsecurity, reloptions, relacl) AS line
          FROM t
        UNION ALL
        SELECT concat_ws(' ', 'column', t.relname, a.attname, a.attnum, a.atttypid, a.atttypmod,
                         a.attnotnull, a.attisdropped, a.attidentity, a.attgenerated, a.attcollation)
          FROM pg_attribute a JOIN t ON t.oid = a.attrelid WHERE a.attnum > 0
        UNION ALL
        SELECT concat_ws(' ', 'default', t.relname, d.adnum, d.adbin)
          FROM pg_attrdef d JOIN t ON t.oid = d.adrelid
        UNION ALL
        SELECT concat_ws(' ', 'constraint', t.relname, k.conname, k.contype, k.condeferrable, k.condeferred,
                         k.convalidated, k.conkey, k.confrelid, k.confkey, k.confupdtype, k.confdeltype,
                         k.confmatchtype, k.conbin)
          FROM pg_constraint k JOIN t ON t.oid = k.conrelid
        UNION ALL
        SELECT concat_ws(' ', 'index', t.relname, ic.relname, i.indisunique, i.indisprimary, i.indkey,
                         i.indclass, i.indexprs, i.indpred)
          FROM pg_index i JOIN t ON t.oid = i.indrelid JOIN pg_class ic ON ic.oid = i.indexrelid
        UNION ALL
        SELECT concat_ws(' ', 'trigger', t.relname, g.tgname, g.tgfoid, g.tgtype, g.tgenabled)
          FROM pg_trigger g JOIN t ON t.oid = g.tgrelid
    ) lines
    """
)

# A table with no pages has held no row since it was created or truncated;
# every other one gets new storage. Cheaper than asking each table for rows.
_TABLES_WITH_PAGES = text(
    """
    SELECT relname FROM pg_class
    WHERE relnamespace = current_schema()::regnamespace AND relkind = 'r' AND relname = ANY(:names)
      AND pg_relation_size(oid) > 0
    """
)


class _PostgresSchema:
    """Builds, resets and finally drops the session's schema, over one
    connection of its own that lives for the session: its catalog caches stay
    warm, where a test's new connection starts cold. Tests never see it; it
    holds no transaction between resets."""

    def __init__(self) -> None:
        self._engine = _make_engine()
        self._names = [table.name for table in Base.metadata.sorted_tables]
        self._signature: str | None = None

    def build(self) -> None:
        with self._engine.begin() as conn:
            self._build(conn)

    def reset(self) -> None:
        with self._engine.begin() as conn:
            # Nothing else uses this database, so waiting for a lock means an
            # earlier test left a session open in a transaction. Fail with
            # "lock timeout" instead of hanging the run (DROP TABLE used to).
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            if self._signature_of(conn) != self._signature:
                self._build(conn)
                return
            occupied = conn.execute(_TABLES_WITH_PAGES, {"names": self._names}).scalars().all()
            if occupied:
                # CASCADE: Postgres truncates a table that others reference
                # only together with them.
                names = ", ".join(conn.dialect.identifier_preparer.quote(name) for name in occupied)
                conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

    def drop(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            Base.metadata.drop_all(conn)
        self._engine.dispose()

    def _build(self, conn) -> None:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
        preparer = conn.dialect.identifier_preparer
        for table in Base.metadata.sorted_tables:
            # A table now lives for the whole session, not for one test. With
            # autovacuum off it never holds a lock TRUNCATE has to wait for,
            # and never gathers statistics a newly created table would not have.
            conn.execute(
                text(
                    f"ALTER TABLE {preparer.format_table(table)} "
                    "SET (autovacuum_enabled = off, toast.autovacuum_enabled = off)"
                )
            )
        self._signature = self._signature_of(conn)

    def _signature_of(self, conn) -> str:
        return conn.execute(_SCHEMA_SIGNATURE, {"names": self._names}).scalar_one()


@pytest.fixture(scope="session")
def _postgres_schema():
    if not _TEST_DATABASE_URL:
        yield None
        return
    schema = _PostgresSchema()
    schema.build()
    yield schema
    schema.drop()


@pytest.fixture()
def engine(_postgres_schema):
    eng = _make_engine()
    if _postgres_schema is None:
        Base.metadata.create_all(eng)
        yield eng
        Base.metadata.drop_all(eng)
    else:
        _postgres_schema.reset()
        yield eng
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
