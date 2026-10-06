"""Two `alembic upgrade heads` at the same moment must both succeed (KAN-92, CI only).

The web service and the outbox worker both migrate on start (render.yaml,
docker-compose.yml), and so does the Dockerfile CMD of every web replica.
alembic/env.py serialises them with a Postgres advisory lock; this script is
the proof, and fails on an env.py without the lock.

Each trial creates its own throwaway database next to DMS_DATABASE_URL's,
starts two `alembic upgrade heads` subprocesses 0 and 0.2 s apart, and drops
the database afterwards. Two scenarios per trial:

- empty: a brand-new database (first deploy, `make up` cold start);
- pending: a database first upgraded to d2f7b0e9c453 (the revision CI's
  migration-smoke-test downgrades to), so the two processes race over real,
  not-yet-applied migrations (a deploy that ships one).

Asserts schema state, not only exit codes: a lock taken in the wrong place
makes Alembic leave its transaction uncommitted, so `upgrade heads` exits 0
with no tables and no alembic_version (measured, see the ticket). Both
processes must exit 0, alembic_version must hold exactly the heads `alembic
heads` reports, and every table on Base.metadata must exist.

Usage: DMS_DATABASE_URL=... python scripts/check_concurrent_alembic_upgrade.py [--repeat N]
"""

import argparse
import os
import subprocess
import sys
import time
import uuid

import psycopg
from sqlalchemy.engine import make_url

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.config import get_settings
from app.db import Base, with_psycopg_driver

PENDING_FROM_REVISION = "d2f7b0e9c453"
SECOND_PROCESS_OFFSET_S = 0.2
UPGRADE_TIMEOUT_S = 300


def libpq_url(sqlalchemy_url: str, database: str) -> str:
    """The same server and credentials as sqlalchemy_url, pointed at another database, in libpq form."""
    url = make_url(with_psycopg_driver(sqlalchemy_url)).set(drivername="postgresql", database=database)
    return url.render_as_string(hide_password=False)


def sqlalchemy_url_for(sqlalchemy_url: str, database: str) -> str:
    url = make_url(with_psycopg_driver(sqlalchemy_url)).set(database=database)
    return url.render_as_string(hide_password=False)


def alembic(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *args], env=env, capture_output=True, text=True, timeout=UPGRADE_TIMEOUT_S, check=False
    )


def expected_heads(env: dict[str, str]) -> set[str]:
    out = alembic(["heads"], env)
    if out.returncode != 0:
        sys.exit("`alembic heads` failed:\n" + out.stderr)
    return {line.split()[0] for line in out.stdout.splitlines() if line.strip()}


def run_trial(base_url: str, scenario: str, heads: set[str]) -> list[str]:
    """One scenario on its own throwaway database; returns the problems found (empty = pass)."""
    name = "dms_concurrent_upgrade_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base_url, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    env = dict(os.environ, DMS_DATABASE_URL=sqlalchemy_url_for(base_url, name))
    problems: list[str] = []
    try:
        if scenario == "pending":
            prep = alembic(["upgrade", PENDING_FROM_REVISION], env)
            if prep.returncode != 0:
                return [
                    f"preparing: `alembic upgrade {PENDING_FROM_REVISION}` exited {prep.returncode}:\n{prep.stderr[-2000:]}"
                ]

        procs = []
        for offset in (0.0, SECOND_PROCESS_OFFSET_S):
            time.sleep(offset)
            procs.append(
                subprocess.Popen(
                    ["alembic", "upgrade", "heads"],
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            )
        for i, proc in enumerate(procs, start=1):
            output, _ = proc.communicate(timeout=UPGRADE_TIMEOUT_S)
            if proc.returncode != 0:
                lines = output.strip().splitlines()
                # From the final exception line on (it carries the failing SQL); else the last few lines.
                starts = [n for n, line in enumerate(lines) if line.startswith("sqlalchemy.exc.")]
                tail = "\n".join(lines[starts[-1] :][:8] if starts else lines[-6:])
                problems.append(f"process {i} exited {proc.returncode}:\n{tail}")

        with psycopg.connect(libpq_url(base_url, name)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            }
            versions = set()
            if "alembic_version" in tables:
                versions = {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}
        if versions != heads:
            problems.append(f"alembic_version holds {sorted(versions)} but `alembic heads` reports {sorted(heads)}")
        missing = sorted(set(Base.metadata.tables) - tables)
        if missing:
            problems.append(f"{len(missing)} of {len(Base.metadata.tables)} mapped tables missing, e.g. {missing[:5]}")
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=1, help="trials per scenario (default 1)")
    args = parser.parse_args()

    base_url = get_settings().database_url
    if make_url(with_psycopg_driver(base_url)).get_backend_name() != "postgresql":
        sys.exit("DMS_DATABASE_URL must point at Postgres - the lock under test is Postgres-only")
    heads = expected_heads(dict(os.environ))
    print(
        "heads: {}; mapped tables: {}; second process starts {} s after the first".format(
            ", ".join(sorted(heads)), len(Base.metadata.tables), SECOND_PROCESS_OFFSET_S
        )
    )

    failures = 0
    for scenario in ("empty", "pending"):
        for trial in range(1, args.repeat + 1):
            started = time.monotonic()
            problems = run_trial(base_url, scenario, heads)
            label = f"{scenario} trial {trial}/{args.repeat} ({time.monotonic() - started:.1f} s)"
            if problems:
                failures += 1
                print("FAIL " + label)
                for problem in problems:
                    print("  " + problem.replace("\n", "\n    "))
            else:
                print("ok   " + label)
    total = 2 * args.repeat
    print(f"{total - failures} of {total} trials passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
