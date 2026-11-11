"""KAN-179 (ADR-134): the migration lock outlives a migration's autocommit block.

`alembic/env.py` serialises concurrent `alembic upgrade heads` runs (KAN-92).
Its first lock was transaction-scoped, so a migration that commits mid-run —
`op.get_context().autocommit_block()`, which `CREATE INDEX CONCURRENTLY`
needs — released it: a second upgrader started the same migration while the
first was still inside it (KAN-86's `023e486` failed 6/6 that way).

The lock is now held by the connection for the whole run. This test proves it
with a script directory of one migration that commits halfway — create a
table, an autocommit block that sleeps and builds an index CONCURRENTLY, then
an insert — run by the real `env.py` (a symlink) twice at once on a
throwaway database. Under a transaction-scoped lock the second run enters the
migration once the first one's opening transaction commits and fails on the
table that already exists; under the connection's lock it waits and finds
the schema current.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import time
import uuid

import psycopg
import pytest

from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

# The key alembic/env.py locks on (KAN-92); env.py is a script, not a module, so it is repeated here.
MIGRATION_ADVISORY_LOCK_KEY = -7906448801533702249

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")

_MIGRATION = '''
"""a migration that commits halfway (KAN-179 test)"""

from alembic import op

revision = "kan179000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE kan179_probe (id integer NOT NULL)")
    with op.get_context().autocommit_block():
        op.execute("SELECT pg_sleep(1.5)")
        op.execute("CREATE INDEX CONCURRENTLY ix_kan179_probe_id ON kan179_probe (id)")
    op.execute("INSERT INTO kan179_probe (id) VALUES (1)")


def downgrade() -> None:
    op.execute("DROP TABLE kan179_probe")
'''


@pytest.fixture
def probe_database():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan179_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield sqlalchemy_url_for(base, name), libpq_url(base, name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def probe_script_directory(tmp_path):
    scripts = tmp_path / "alembic"
    (scripts / "versions").mkdir(parents=True)
    (scripts / "env.py").symlink_to(_REPO_ROOT / "alembic" / "env.py")
    (scripts / "versions" / "kan179000001_probe.py").write_text(_MIGRATION, encoding="utf-8")
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        textwrap.dedent(
            f"""\
            [alembic]
            script_location = {scripts}
            prepend_sys_path = {_REPO_ROOT}
            sqlalchemy.url = driver://placeholder

            [loggers]
            keys = root

            [handlers]
            keys = console

            [formatters]
            keys = generic

            [logger_root]
            level = WARNING
            handlers = console

            [handler_console]
            class = StreamHandler
            args = (sys.stderr,)
            formatter = generic

            [formatter_generic]
            format = %%(levelname)s %%(message)s
            """
        ),
        encoding="utf-8",
    )
    return ini


def test_a_second_upgrader_waits_for_the_whole_run_across_an_autocommit_block(probe_database, probe_script_directory):
    sqlalchemy_url, libpq = probe_database
    env = dict(os.environ, DMS_DATABASE_URL=sqlalchemy_url)
    command = [sys.executable, "-m", "alembic", "-c", str(probe_script_directory), "upgrade", "heads"]

    first = subprocess.Popen(command, cwd=_REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with psycopg.connect(libpq, autocommit=True) as watcher:
        # Wait until the first run is inside its autocommit block (its opening
        # transaction, with the table, has committed) — not a fixed delay.
        deadline = time.monotonic() + 60
        while not watcher.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND query LIKE 'SELECT pg_sleep%%'"
        ).fetchone()[0]:
            assert first.poll() is None, first.communicate()[1][-3000:]
            assert time.monotonic() < deadline, "the first run never reached its autocommit block"
            time.sleep(0.05)

        second = subprocess.Popen(
            command, cwd=_REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # A write goes through while the migration's block runs: no lock of
        # the upgrade is held on the table across it (the point of CONCURRENTLY).
        watcher.execute("SET statement_timeout = '500ms'")
        watcher.execute("INSERT INTO kan179_probe (id) VALUES (2)")
        # The first run still holds the migration key in this database after
        # its opening transaction committed, so a second run cannot enter
        # (whether it has reached its first poll yet or not). Other databases
        # (parallel workers migrating their own) do not count.
        held = watcher.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND objid = %s"
            " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
            (MIGRATION_ADVISORY_LOCK_KEY & 0xFFFFFFFF,),
        ).fetchone()
        assert held == (1,), "the first run holds the migration lock during its autocommit block"
        assert first.poll() is None, "the first run finished during the check: the trial proves nothing"
    outcomes = [process.communicate(timeout=UPGRADE_TIMEOUT_S) for process in (first, second)]

    assert [first.returncode, second.returncode] == [0, 0], [stderr[-6000:] for _, stderr in outcomes]
    with psycopg.connect(libpq) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("kan179000001",)]
        assert connection.execute("SELECT id FROM kan179_probe ORDER BY id").fetchall() == [(1,), (2,)]
        index = connection.execute(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = 'ix_kan179_probe_id'"
        ).fetchall()
        assert index == [(True,)], "the CONCURRENTLY index was built once, and valid"
        # The lock is released with the run: nothing holds the key afterwards.
        held = connection.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database = "
            "(SELECT oid FROM pg_database WHERE datname = current_database())"
        ).fetchone()
        assert held == (0,)


def test_the_key_here_is_env_pys():
    assert f"MIGRATION_ADVISORY_LOCK_KEY = {MIGRATION_ADVISORY_LOCK_KEY}" in (_REPO_ROOT / "alembic" / "env.py").read_text()
