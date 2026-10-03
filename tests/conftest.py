import gc
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
# Marks this run's own connections, so _PostgresSchema only ever terminates those.
_APPLICATION_NAME = f"nexotec-tests-{os.getpid()}"


def _make_engine():
    if _TEST_DATABASE_URL:
        return create_engine(
            _TEST_DATABASE_URL, pool_pre_ping=True, connect_args={"application_name": _APPLICATION_NAME}
        )
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
# testing anything. Each test still starts where create_all left it, and
# _PostgresSchema makes sure of it:
# - before every test, every table is empty in new storage (TRUNCATE, not
#   DELETE: no dead rows pile up over the session, and the table is what a
#   newly created one is) and identity sequences are at their start;
# - after any DDL in the test database (an event trigger counts it), the
#   schema is rebuilt, so a test that changes it changes nothing for the next
#   (an object a test leaves that depends on a test table, a view or a child
#   table, makes the rebuild's drop_all fail loudly, as drop_all did before);
# - after every test, a session of it still holding a lock on a test table
#   fails that test and is terminated, so the rest of the run goes on
#   (drop_all used to hang on it).
# The SQLite fast lane keeps a fresh in-memory database per test.

# Every DDL command on this database's objects is counted: columns,
# constraints, indexes, triggers, rules, policies, inheritance, views, grants,
# comments. TRUNCATE is not DDL and is not counted; nor are commands on
# databases, roles and tablespaces, which an event trigger never sees. It needs
# a superuser, which the test role is in CI, in docker compose and in cloud
# sessions (scripts/dev/cloud-postgres).
_STOP_COUNTING_DDL = (
    "DROP EVENT TRIGGER IF EXISTS nexotec_tests_count_ddl",
    "DROP FUNCTION IF EXISTS nexotec_tests_count_ddl()",
    "DROP SEQUENCE IF EXISTS nexotec_tests_ddl_count",
)
_COUNT_DDL = _STOP_COUNTING_DDL + (
    "CREATE SEQUENCE nexotec_tests_ddl_count",
    (
        "CREATE FUNCTION nexotec_tests_count_ddl() RETURNS event_trigger LANGUAGE plpgsql"
        " AS $$ BEGIN PERFORM nextval('nexotec_tests_ddl_count'); END $$"
    ),
    "CREATE EVENT TRIGGER nexotec_tests_count_ddl ON ddl_command_end EXECUTE FUNCTION nexotec_tests_count_ddl()",
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

# This run's other sessions that hold a lock on a test table. Between tests
# there must be none: drop_all, which needs all those locks, would have
# waited for one forever.
_LOCK_HOLDERS = text(
    """
    SELECT DISTINCT a.pid, c.relname FROM pg_locks l
      JOIN pg_stat_activity a ON a.pid = l.pid
      JOIN pg_class c ON c.oid = l.relation
    WHERE l.locktype = 'relation' AND l.granted AND a.pid <> pg_backend_pid()
      AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND a.application_name = :application_name
      AND c.relnamespace = current_schema()::regnamespace AND c.relname = ANY(:names)
    """
)


class _PostgresSchema:
    """Builds, resets and finally drops the session's schema, over a
    connection of its own that lives for the session: its catalog caches stay
    warm, where a test's new connection starts cold. Tests never see it; it
    holds no transaction between resets."""

    def __init__(self) -> None:
        self._engine = _make_engine()
        self._names = [table.name for table in Base.metadata.sorted_tables]
        self._ddl_count: int | None = None

    def build(self) -> None:
        with self._engine.begin() as conn:
            if not conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar_one():
                raise RuntimeError(
                    "The Postgres test lane needs a superuser test role, as CI, docker compose and "
                    "scripts/dev/cloud-postgres give it: without one, DDL run by a test goes unnoticed."
                )
            for statement in _COUNT_DDL:
                conn.execute(text(statement))
            self._build(conn)

    def reset(self) -> None:
        with self._engine.begin() as conn:
            # This run's own leftover sessions are ended after their test, so
            # waiting here means another process uses this database. Fail with
            # "lock timeout" instead of hanging the run.
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            if self._ddl_count_of(conn) != self._ddl_count:
                self._build(conn)
                return
            occupied = conn.execute(_TABLES_WITH_PAGES, {"names": self._names}).scalars().all()
            if occupied:
                # CASCADE: Postgres truncates a table that others reference
                # only together with them.
                names = ", ".join(conn.dialect.identifier_preparer.quote(name) for name in occupied)
                conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

    def check_sessions_closed(self, test: str) -> None:
        holders = self._lock_holders()
        if holders:
            gc.collect()  # a session left only for the garbage collector to close holds nothing after this
            holders = self._lock_holders()
        if not holders:
            return
        with self._engine.begin() as conn:
            for pid in sorted({pid for pid, _ in holders}):
                conn.execute(text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": pid})
        tables = ", ".join(sorted({table for _, table in holders}))
        raise RuntimeError(
            f"{test} left a database session open in a transaction, holding locks on: {tables}. "
            "Its connection was terminated so the remaining tests can run; close every session a test opens."
        )

    def drop(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            for statement in _STOP_COUNTING_DDL:
                conn.execute(text(statement))
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
        self._ddl_count = self._ddl_count_of(conn)

    def _ddl_count_of(self, conn) -> int:
        return conn.execute(text("SELECT last_value FROM nexotec_tests_ddl_count")).scalar_one()

    def _lock_holders(self) -> list[tuple[int, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(_LOCK_HOLDERS, {"application_name": _APPLICATION_NAME, "names": self._names})
            return [(pid, table) for pid, table in rows]


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
def engine(_postgres_schema, request):
    eng = _make_engine()
    if _postgres_schema is None:
        Base.metadata.create_all(eng)
        yield eng
        Base.metadata.drop_all(eng)
        eng.dispose()
        return
    _postgres_schema.reset()
    yield eng
    eng.dispose()
    _postgres_schema.check_sessions_closed(request.node.nodeid)


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
