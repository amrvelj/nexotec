"""KAN-216 (FR-A-47): the catalogue the migrations seed is the catalogue the
code declares.

`notification_event` is seeded by a platform migration with literal rows (a
migration never reads the application's declaration, which moves on), while
`app/platform/services/notification_catalogue.py` is what call sites name
and what the test schema is seeded from (`tests/conftest.py`). A module that
registers an event in one and forgets the other fails here: in production
`notify` would refuse the code, or a row would exist that nothing renders.

Runs `alembic upgrade heads` on a throwaway database next to the test
database, as `tests/test_migrated_enum_checks.py` does.
"""

import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.platform.services.notification_catalogue import CATALOGUE
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


@pytest.fixture(scope="module")
def migrated_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan216_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = sqlalchemy_url_for(base, name)
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "heads"],
            cwd=_REPO_ROOT,
            env=dict(os.environ, DMS_DATABASE_URL=url),
            capture_output=True,
            text=True,
            timeout=UPGRADE_TIMEOUT_S,
            check=False,
        )
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _declared() -> set[tuple]:
    return {
        (s.code.value, s.family.value, s.default_timing.value, s.switchable, s.title_key, s.body_key)
        for s in CATALOGUE
    }


def test_the_migrated_catalogue_is_the_declared_one(migrated_url):
    engine = create_engine(migrated_url)
    try:
        with engine.connect() as conn:
            rows = {
                tuple(r)
                for r in conn.execute(
                    text("SELECT code, family, default_timing, switchable, title_key, body_key FROM notification_event")
                )
            }
    finally:
        engine.dispose()
    assert rows == _declared()
    assert len(rows) == 12  # the security family of v1 (D-A-40)
