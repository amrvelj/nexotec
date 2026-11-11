"""KAN-627: under pytest-xdist every worker runs on a Postgres database of its
own, `<name>_gw0`, `<name>_gw1` ... (tests/conftest.py). One database would
have each worker's TRUNCATE empty the tables under the others' tests.

The first test looks at the session it runs in; the second runs a real
`pytest -n 2` session on a scratch database and looks at what each worker got
and at what the run left behind.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

pytestmark = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Postgres lane only (ADR-011): the SQLite fast lane has one in-memory database per test.",
)

_REPO = Path(__file__).resolve().parent.parent


def test_this_sessions_database_is_its_workers_own(engine, request):
    """In a worker: the engine, the server and DMS_TEST_DATABASE_URL all name the
    worker's database. In a plain run there is no worker and no suffix."""
    worker = getattr(request.config, "workerinput", {}).get("workerid")
    with engine.connect() as conn:
        connected_to = conn.execute(text("SELECT current_database()")).scalar_one()

    assert engine.url.database == connected_to == make_url(os.environ["DMS_TEST_DATABASE_URL"]).database
    if worker:
        assert connected_to.endswith(f"_{worker}"), connected_to
    else:
        assert not connected_to.rpartition("_")[2].startswith("gw"), connected_to


_WORKER_MODULE = '''
import os
from pathlib import Path

from sqlalchemy import text


def test_where_am_i(engine, request):
    worker = request.config.workerinput["workerid"]
    with engine.connect() as conn:
        database = conn.execute(text("SELECT current_database()")).scalar_one()
        tables = conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()")).scalar_one()
    Path(os.environ["KAN627_OUT"], worker).write_text(f"{database} {tables}")
'''


def test_a_parallel_run_gives_each_worker_a_database_and_leaves_none_behind(tmp_path):
    url = make_url(os.environ["DMS_TEST_DATABASE_URL"])
    # A short name of its own, not this checkout's database name plus a suffix: Postgres cuts a
    # name at 63 bytes, and a long worktree name would lose the `_gw<n>` that tells workers apart.
    database = f"dms_kan627_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    module = tmp_path / "test_where.py"
    module.write_text(_WORKER_MODULE)
    out = tmp_path / "out"
    out.mkdir()

    def databases() -> list[str]:
        with admin.connect() as conn:
            rows = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"), {"p": database + "%"})
            return sorted(name for (name,) in rows)

    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        # --dist each: both workers run the one test, so both databases are used whatever the scheduling.
        # The environment is this process's own: a worker's PYTEST_XDIST_WORKER is inherited, and must
        # not add a suffix of its own to the inner run's databases.
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "tests.conftest", "--noconftest", "-p", "no:cacheprovider",
             "-q", "-n", "2", "--dist", "each", str(module)],
            cwd=_REPO,
            env={
                **os.environ,
                "DMS_TEST_DATABASE_URL": url.set(database=database).render_as_string(False),
                "KAN627_OUT": str(out),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        seen = {path.name: path.read_text().split() for path in out.iterdir()}
        after = databases()
    finally:
        with admin.connect() as conn:
            for name in databases():
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()

    assert sorted(seen) == ["gw0", "gw1"], output
    assert [seen[worker][0] for worker in ("gw0", "gw1")] == [f"{database}_gw0", f"{database}_gw1"]
    # Each worker's database holds the schema, built the way the single database's is.
    assert all(int(tables) > 50 for _, tables in seen.values()), seen
    # The workers' databases are dropped at the end; the one the run was given is untouched.
    assert after == [database]
