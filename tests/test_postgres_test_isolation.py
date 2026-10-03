"""KAN-133: the Postgres lane builds its schema once per session
(tests/conftest.py, _PostgresSchema), so what create_all + drop_all around
every test used to give each test by construction is pinned here: empty
tables, the schema exactly as create_all makes it whatever a test did to it,
and no session of one test left holding locks into the next.

Each test calls what the fixtures run between two tests and then looks at
what the next test would see.
"""

import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.platform.models.dealership import DealerGroup

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Postgres lane only (ADR-011): the SQLite fast lane builds a fresh in-memory database per test.",
)


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table.name: conn.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }


def _oid(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT to_regclass(:t)::oid"), {"t": table}).scalar_one()


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


def test_a_session_left_open_in_a_transaction_fails_its_test_and_is_ended(engine, _postgres_schema):
    left_open = sessionmaker(bind=engine)()
    left_open.execute(select(func.count()).select_from(DealerGroup))  # a lock, kept until the transaction ends

    with pytest.raises(RuntimeError, match="tests/the_test.py::test_x left a database session open .* dealer_group"):
        _postgres_schema.check_sessions_closed("tests/the_test.py::test_x")

    with pytest.raises(OperationalError):  # its connection was terminated
        left_open.execute(text("SELECT 1"))
    left_open.close()
    _postgres_schema.check_sessions_closed("tests/the_test.py::test_x")  # nothing is left
