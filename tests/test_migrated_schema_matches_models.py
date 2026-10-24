"""KAN-170 / KAN-251: a database built by the migrations is exactly the schema
the models declare — tables, columns, types, nullability, indexes (their
full stored definition, a partial index's WHERE included, since KAN-414),
unique constraints, foreign keys and comments.

Every other test runs against `create_all` of the models, so the migrations
can drift from the models (a migration-only index, a constraint the model
names differently, a comment one side lacks) without any test noticing,
while deployed databases carry the drifted shape and the next
`alembic revision --autogenerate` proposes to "fix" it. Anto's ruling on
KAN-251: one gate, no allowlist — the migrations bring the database in line
with the code until it is green with no exceptions.

The comparison is Alembic's own (`compare_metadata`, what `alembic check`
and `--autogenerate` run), configured as alembic/env.py configures it
(tests/schema_diff_support.py), in a fresh interpreter that imports
app.model_registry and nothing else; the real `alembic check` command runs
too, so the gate never passes what the CLI reports (the gate is the stricter of
the two since KAN-414: it also compares every index's stored definition,
which `alembic check` does not). Like
tests/test_migrated_enum_checks.py (KAN-296, which this test does not
replace: autogenerate does not compare CHECK constraints), it runs
`alembic upgrade heads` on a throwaway database next to the test database.
Postgres-only (ADR-011): SQLite's reflection reports none of this faithfully.
"""

import json
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]  # alembic.ini

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _alembic(args: list[str], url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ, DMS_DATABASE_URL=url),
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )


def _compare_cli(url: str, *flags: str) -> subprocess.CompletedProcess[str]:
    """tests/schema_diff_support.py's command line, in a fresh interpreter —
    the same invocation CI's migration smoke test runs with `--fail`."""

    return subprocess.run(
        [sys.executable, "-m", "tests.schema_diff_support", url, *flags],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )


def schema_differences(url: str) -> list[str]:
    """tests/schema_diff_support.py's comparison, run in a fresh interpreter."""

    probe = _compare_cli(url)
    assert probe.returncode == 0, probe.stderr[-3000:]
    return json.loads(probe.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def migrated_url():
    """A database built by `alembic upgrade heads` from empty, dropped afterwards."""

    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = f"{make_url(base).database}_kan251_{uuid.uuid4().hex[:12]}"
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = sqlalchemy_url_for(base, name)
        upgrade = _alembic(["upgrade", "heads"], url)
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_a_migrated_database_is_exactly_the_schema_the_models_declare(migrated_url):
    found = schema_differences(migrated_url)

    assert found == [], f"{len(found)} difference(s) between the migrated schema and the models:\n" + "\n".join(found)


def test_alembic_check_passes_on_a_migrated_database(migrated_url):
    """The CLI's own verdict, through alembic/env.py's configuration — what a
    developer runs by hand and what `alembic revision --autogenerate` is
    based on. Everything it reports the comparison above reports too; the
    comparison also sees index definitions (KAN-414)."""

    check = _alembic(["check"], migrated_url)
    assert check.returncode == 0, check.stdout[-3000:] + check.stderr[-3000:]


def test_a_drifted_database_is_reported(migrated_url):
    """The comparison itself goes red: one index dropped, one unique
    constraint added and one column comment changed on the migrated
    database — undone afterwards."""

    engine = create_engine(migrated_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX ix_customer_group_id_last_name"))
            conn.execute(text('ALTER TABLE "user" ADD CONSTRAINT uq_user_phone_drift UNIQUE (phone)'))
            conn.execute(text("COMMENT ON COLUMN stock_item.vehicle_id IS 'drifted'"))
        try:
            found = schema_differences(migrated_url)
            # CI's step (`--fail`, KAN-251) goes red on the same drift: exit 1,
            # the differences still on stdout for the job log.
            failing = _compare_cli(migrated_url, "--fail")
            assert failing.returncode == 1, failing.stderr[-3000:]
            assert json.loads(failing.stdout.strip().splitlines()[-1]) == found
        finally:
            with engine.begin() as conn:
                conn.execute(text(
                    "COMMENT ON COLUMN stock_item.vehicle_id IS "
                    "'Owned by the vehicle context (VehicleMdm.id). No DB-level FK.'"
                ))
                conn.execute(text('ALTER TABLE "user" DROP CONSTRAINT uq_user_phone_drift'))
                conn.execute(text("CREATE INDEX ix_customer_group_id_last_name ON customer (group_id, last_name)"))
    finally:
        engine.dispose()

    # Back in step, `--fail` is quiet: exit 0 and an empty list.
    restored = _compare_cli(migrated_url, "--fail")
    assert restored.returncode == 0, restored.stderr[-3000:]
    assert json.loads(restored.stdout.strip().splitlines()[-1]) == []

    assert found == [
        "add_index: index ix_customer_group_id_last_name on customer (customer.group_id, customer.last_name)",
        (
            "modify_comment: stock_item.vehicle_id: migrated 'drifted', "
            "model 'Owned by the vehicle context (VehicleMdm.id). No DB-level FK.'"
        ),
        "remove_constraint: UniqueConstraint uq_user_phone_drift on user (phone)",
    ], found


def test_a_partial_index_predicate_that_differs_is_reported(migrated_url):
    """KAN-414: the KAN-201 review's probe. `compare_metadata` does not compare
    `postgresql_where`, so `uq_user_connected_account_live` recreated without
    its predicate, or with another one, printed no difference; the index
    comparison (`pg_get_indexdef` against the models' own `create_all`) does.
    Restored afterwards from its own stored definition."""

    engine = create_engine(migrated_url)
    index = "uq_user_connected_account_live"
    try:
        with engine.connect() as conn:
            original = conn.execute(text(f"SELECT pg_get_indexdef('{index}'::regclass)")).scalar_one()
        assert "WHERE" in original, original
        unpredicated = original.split(" WHERE ")[0]
        found: dict[str, list[str]] = {}
        try:
            for probe, definition in (
                ("dropped", unpredicated),
                ("changed", f"{unpredicated} WHERE (status = 'active'::text)"),
            ):
                with engine.begin() as conn:
                    conn.execute(text(f"DROP INDEX {index}"))
                    conn.execute(text(definition))
                found[probe] = schema_differences(migrated_url)
            failing = _compare_cli(migrated_url, "--fail")
            assert failing.returncode == 1, failing.stderr[-3000:]
        finally:
            with engine.begin() as conn:
                conn.execute(text(f"DROP INDEX IF EXISTS {index}"))
                conn.execute(text(original))
    finally:
        engine.dispose()

    migrated, model = unpredicated.replace("public.", ""), original.replace("public.", "")
    assert found["dropped"] == [
        f"modify_index: user_connected_account.{index}: migrated {migrated!r}, model {model!r}"
    ], found["dropped"]
    # Postgres stores the probe's predicate in its own normal form.
    assert len(found["changed"]) == 1, found["changed"]
    assert "WHERE ((status)::text = 'active'::text)" in found["changed"][0], found["changed"]
    assert schema_differences(migrated_url) == []
