"""KAN-643: the database refuses a vehicle_party period that ends before it starts.

KAN-583 fixed the two code paths that wrote an inverted period; `ck_vehicle_party_period`
(`effective_to IS NULL OR effective_to >= effective_from`) makes one impossible on every
path (ADR-064, rule 10). A merge copy closed at its own start (KAN-177/KAN-672,
`effective_to = effective_from`) and an open period stay allowed.

The second half runs the migration itself on a throwaway database: its downgrade drops
the constraint, and its upgrade stops with a message, adding nothing, while an inverted
row exists — correcting such a row rewrites history, which is Anto's decision.
"""

import datetime as dt
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REVISION = "c7e2a9f4b1d3"
_DOWN_REVISION = "a4c1e6b9d2f7"
_T0 = dt.datetime(2026, 3, 1, 9, 0, tzinfo=dt.UTC)

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _party(effective_to: dt.datetime | None) -> VehicleParty:
    return VehicleParty(
        vehicle_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        role=VehiclePartyRole.OWNER,
        effective_from=_T0,
        effective_to=effective_to,
    )


def test_an_inverted_period_is_refused_on_insert(db_session):
    db_session.add(_party(_T0 - dt.timedelta(days=1)))
    with pytest.raises(IntegrityError, match="ck_vehicle_party_period"):
        db_session.commit()
    db_session.rollback()


def test_an_update_that_inverts_a_period_is_refused(db_session):
    row = _party(None)
    db_session.add(row)
    db_session.commit()
    row.effective_to = _T0 - dt.timedelta(seconds=1)
    with pytest.raises(IntegrityError, match="ck_vehicle_party_period"):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "effective_to",
    [None, _T0, _T0 + dt.timedelta(days=30)],
    ids=["open", "closed-at-its-own-start (merge copy)", "closed-later"],
)
def test_open_zero_length_and_forward_periods_are_allowed(db_session, effective_to):
    db_session.add(_party(effective_to))
    db_session.commit()
    assert db_session.query(VehicleParty).count() == 1


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ, DMS_DATABASE_URL=url),
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )


@pytest.fixture(scope="module")
def migrated_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan643_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = sqlalchemy_url_for(base, name)
        upgrade = _alembic(url, "upgrade", "heads")
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _constraint_exists(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_vehicle_party_period'")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_the_migration_refuses_to_run_over_an_inverted_row_and_adds_nothing(migrated_url):
    assert _constraint_exists(migrated_url)
    down = _alembic(migrated_url, "downgrade", _DOWN_REVISION)
    assert down.returncode == 0, down.stderr[-3000:]
    assert not _constraint_exists(migrated_url)

    engine = create_engine(migrated_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO vehicle_party (id, vehicle_id, customer_id, role, effective_from, effective_to, "
                    "created_at, updated_at) VALUES (:id, :v, :c, 'owner', :f, :t, now(), now())"
                ),
                {"id": uuid.uuid4(), "v": uuid.uuid4(), "c": uuid.uuid4(), "f": _T0, "t": _T0 - dt.timedelta(days=1)},
            )
        refused = _alembic(migrated_url, "upgrade", _REVISION)
        assert refused.returncode != 0
        assert "KAN-643: vehicle_party holds 1 row(s)" in refused.stderr
        assert not _constraint_exists(migrated_url)

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM vehicle_party"))
    finally:
        engine.dispose()
    again = _alembic(migrated_url, "upgrade", "heads")
    assert again.returncode == 0, again.stderr[-3000:]
    assert _constraint_exists(migrated_url)
