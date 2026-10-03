"""KAN-133: the Postgres lane builds its schema once per session
(tests/conftest.py, _PostgresSchema), so what create_all + drop_all around
every test used to give each test by construction is pinned here: empty
tables with no planner state, the schema exactly as create_all makes it
whatever a test did to it, and no session of one test left holding locks into
the next.

Most tests call what the fixtures run between two tests and then look at what
the next test would see; the last one runs a real pytest session.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, make_url, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.platform.models.dealership import DealerGroup

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Postgres lane only (ADR-011): the SQLite fast lane builds a fresh in-memory database per test.",
)

_REPO = Path(__file__).resolve().parent.parent


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table.name: conn.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }


def _oid(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT to_regclass(:t)::oid"), {"t": table}).scalar_one()


def _planner_state(engine) -> set[tuple[float, int]]:
    """(reltuples, statistics rows) over every test table."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.reltuples, (SELECT count(*) FROM pg_statistic s WHERE s.starelid = c.oid) FROM pg_class c "
                "WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'r' AND c.relname = ANY(:names)"
            ),
            {"names": [table.name for table in Base.metadata.sorted_tables]},
        )
        return {(reltuples, stats) for reltuples, stats in rows}


def test_rows_a_test_leaves_are_gone_and_the_schema_is_not_rebuilt(engine, db_session, _postgres_schema):
    db_session.add(DealerGroup(name="left behind"))
    db_session.commit()
    oid = _oid(engine, "dealer_group")
    assert _row_counts(engine)["dealer_group"] == 1

    _postgres_schema.reset()

    assert set(_row_counts(engine).values()) == {0}  # every table, the country seed's too
    assert _oid(engine, "dealer_group") == oid  # TRUNCATE is not DDL: no rebuild


def test_ddl_a_test_runs_is_undone_before_the_next(engine, _postgres_schema):
    oid = _oid(engine, "customer")
    with engine.begin() as conn:
        conn.execute(text("CREATE RULE swallow_inserts AS ON INSERT TO reference_value DO INSTEAD NOTHING"))
        conn.execute(text("ALTER TABLE customer DROP COLUMN last_name"))
        conn.execute(text("DROP INDEX ix_vehicle_vin"))

    _postgres_schema.reset()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_rules WHERE rulename = 'swallow_inserts'")).scalar() == 0
        assert conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'customer' AND column_name = 'last_name'"
            )
        ).scalar() == 1
        assert conn.execute(text("SELECT to_regclass('ix_vehicle_vin')")).scalar() is not None
    assert _oid(engine, "customer") != oid  # rebuilt
    assert set(_row_counts(engine).values()) == {0}


def test_ddl_run_as_a_replica_session_is_undone_too(engine, _postgres_schema):
    """An event trigger in its default mode is silent under
    session_replication_role = replica; this DDL is the only DDL here."""
    oid = _oid(engine, "reference_value")
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text("CREATE RULE swallow_inserts AS ON INSERT TO reference_value DO INSTEAD NOTHING"))

    _postgres_schema.reset()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_rules WHERE rulename = 'swallow_inserts'")).scalar() == 0
    assert _oid(engine, "reference_value") != oid


def test_ddl_run_under_a_non_superuser_role_works_and_is_counted(engine, _postgres_schema):
    """The counting trigger runs as whoever ran the DDL; a test that SET ROLEs
    to a plain role must still be able to run DDL, as with create_all per test."""
    role = f"nexotec_tests_plain_{os.getpid()}"  # roles belong to the whole server: removed below
    oid = _oid(engine, "customer")
    with engine.begin() as conn:
        conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        conn.execute(text(f'GRANT CREATE ON SCHEMA public TO "{role}"'))
    try:
        with engine.begin() as conn:
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            conn.execute(text("CREATE TABLE made_by_a_plain_role (x int)"))

        _postgres_schema.reset()

        assert _oid(engine, "customer") != oid  # counted, so rebuilt
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS made_by_a_plain_role"))
            conn.execute(text(f'REVOKE CREATE ON SCHEMA public FROM "{role}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


def test_planner_state_a_test_leaves_is_gone_before_the_next(engine, db_session, _postgres_schema):
    db_session.add(DealerGroup(name="analysed"))
    db_session.commit()
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("ANALYZE dealer_group"))  # statistics, which TRUNCATE keeps
        conn.execute(text("DELETE FROM reference_value"))
        conn.execute(text("VACUUM reference_value"))  # reltuples 0 and, emptied, no pages left
    assert _planner_state(engine) != {(-1, 0)}

    _postgres_schema.reset()

    assert _planner_state(engine) == {(-1, 0)}  # what every newly created table has


def test_a_session_left_open_in_a_transaction_fails_its_test_and_is_ended(engine, _postgres_schema):
    left_open = sessionmaker(bind=engine)()
    left_open.execute(select(func.count()).select_from(DealerGroup))  # a lock, kept until the transaction ends

    with pytest.raises(RuntimeError, match="tests/the_test.py::test_x left a database session open .* dealer_group"):
        _postgres_schema.check_sessions_closed("tests/the_test.py::test_x")

    with pytest.raises(OperationalError):  # its connection was terminated
        left_open.execute(text("SELECT 1"))
    left_open.close()
    _postgres_schema.check_sessions_closed("tests/the_test.py::test_x")  # nothing is left


_LEAKING_MODULE = '''
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

_kept = []


def test_leaves_a_session_open(engine):
    session = sessionmaker(bind=engine)()
    session.execute(text("SELECT count(*) FROM dealer_group"))
    _kept.append(session)


def test_after_it(engine):
    pass
'''


def test_the_engine_fixture_fails_the_test_that_leaves_a_session_open_and_the_run_goes_on(tmp_path):
    """The fixture wiring itself: a real pytest session, in a database of its own."""
    url = make_url(os.environ["DMS_TEST_DATABASE_URL"])
    database = f"{url.database}_isolation_{os.getpid()}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    module = tmp_path / "test_leak.py"
    module.write_text(_LEAKING_MODULE)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "tests.conftest", "--noconftest", "-p", "no:cacheprovider",
             "-q", str(module)],
            cwd=_REPO,
            env={**os.environ, "DMS_TEST_DATABASE_URL": url.set(database=database).render_as_string(False)},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,  # the inner run is meant to report an error; its output is what is checked
        )
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()

    output = result.stdout + result.stderr
    assert re.search(r"\b2 passed\b", output) and re.search(r"\b1 error\b", output), output
    assert re.search(r"^ERROR \S*test_leak\.py::test_leaves_a_session_open\b", output, re.MULTILINE), output
    assert "test_leak.py::test_leaves_a_session_open left a database session open" in output, output
