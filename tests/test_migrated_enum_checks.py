"""KAN-296: a database built by the migrations carries exactly the enum CHECKs
the models declare.

Every StoredEnum column carries `ck_{table}_{column}_enum` (app/core/enum_type.py).
The models attach it, so every other test's schema (`create_all`) has it
whether or not a migration created it, and Alembic's autogenerate does not
compare CHECK constraints. A migration that adds an enum column or member
and forgets the constraint would pass every other test while deployed
databases lack the guard, or keep refusing the new member.

So this test runs `alembic upgrade heads` on a throwaway database next to the
test database, and compares every `ck_*_enum` there — table, name and the
definition as Postgres stores it — with the test database's, which
`create_all` built from the models. KAN-251's general schema-drift gate
(tests/test_migrated_schema_matches_models.py) runs beside it, not instead
of it: Alembic's comparison does not see CHECK constraints.
"""

import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

import app.model_registry  # noqa: F401  registers every model on Base.metadata
from app.core.enum_type import StoredEnum, enum_check_name
from app.db import Base
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.enum_test_support import enum_checks

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]  # alembic.ini

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _differences(models: dict[tuple[str, str], str], migrated: dict[tuple[str, str], str]) -> list[str]:
    found = [
        f"{table}.{name}: the models declare it, the migrations never create it"
        for table, name in sorted(models.keys() - migrated.keys())
    ]
    found += [
        f"{table}.{name}: the migrations create it, no model declares it"
        for table, name in sorted(migrated.keys() - models.keys())
    ]
    found += [
        f"{table}.{name}: migrated {migrated[(table, name)]} but the models declare {models[(table, name)]}"
        for table, name in sorted(models.keys() & migrated.keys())
        if models[(table, name)] != migrated[(table, name)]
    ]
    return found


@pytest.fixture(scope="module")
def migrated_url():
    """A database built by `alembic upgrade heads` from empty, dropped afterwards."""

    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan296_" + uuid.uuid4().hex[:12]
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


def _migrated_checks(url: str) -> dict[tuple[str, str], str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return enum_checks(conn)
    finally:
        engine.dispose()


def test_a_migrated_database_carries_exactly_the_enum_checks_the_models_declare(engine, migrated_url):
    with engine.connect() as conn:
        models = enum_checks(conn)
    declared = {
        (table.name, enum_check_name(table.name, column.name))
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, StoredEnum)
    }
    assert set(models) == declared  # the comparison below is against every enum column, not a subset
    assert len(declared) >= 62

    assert _differences(models, _migrated_checks(migrated_url)) == []


def test_a_missing_or_stale_check_is_reported(engine, migrated_url):
    """The comparison itself goes red: on the migrated database, one CHECK
    dropped and one narrowed to fewer members — rolled back afterwards."""

    with engine.connect() as conn:
        models = enum_checks(conn)
    migrated_engine = create_engine(migrated_url)
    try:
        with migrated_engine.connect() as conn:
            conn.execute(text("ALTER TABLE stock_item DROP CONSTRAINT ck_stock_item_condition_enum"))
            conn.execute(text("ALTER TABLE vehicle_mdm DROP CONSTRAINT ck_vehicle_mdm_vehicle_status_enum"))
            conn.execute(
                text(
                    "ALTER TABLE vehicle_mdm ADD CONSTRAINT ck_vehicle_mdm_vehicle_status_enum "
                    "CHECK (vehicle_status IN ('active'))"
                )
            )
            found = _differences(models, enum_checks(conn))
            conn.rollback()
    finally:
        migrated_engine.dispose()

    assert len(found) == 2, found
    assert found[0] == "stock_item.ck_stock_item_condition_enum: the models declare it, the migrations never create it"
    assert found[1].startswith("vehicle_mdm.ck_vehicle_mdm_vehicle_status_enum: migrated CHECK")
