"""KAN-204 migration 83d8634ff337, run for real on a throwaway Postgres
database, with rows that exist before it:

- `BACKFILL_LOCATIONS_SQL`: every existing location gets `location_types = []`,
  `is_active = true`, and the calendar whose code is its canton's — a ZH site
  gets ZH, an NE site gets NE although Le Landeron has its own, an AG site
  none (Aargau's calendars are per district), a site without a canton none;
- `FIRST_LOCATION_SQL` (FR-A-03): a Betrieb without a location gets one from
  its registered seat (trade name, else legal name; address, phone, canton
  calendar; no code), a Betrieb that has locations gets none, and a second
  run adds nothing;
- the 34 calendars and 302 holiday rules are seeded;
- the downgrade removes the tables and columns and keeps every location row.

The chain above KAN-204 (KAN-205, KAN-208, KAN-216, …) runs too: the test
upgrades to `heads` and asserts only on what this migration owns. The rows
are inserted with SQL, not the models, because the models are the schema at
`heads`, not at `_BEFORE`.
"""

import importlib.util
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.dealership_support import valid_uid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MIGRATION = next((_REPO_ROOT / "alembic" / "versions" / "platform").glob("83d8634ff337_*.py"))


def _load_migration():
    spec = importlib.util.spec_from_file_location("kan204_migration_under_test", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BEFORE = _load_migration().down_revision  # the platform revision KAN-204 descends from

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _alembic(url: str, *args: str) -> None:
    run = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ, DMS_DATABASE_URL=url),
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]


@pytest.fixture
def scratch_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan204_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield sqlalchemy_url_for(base, name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


_GROUP = text(
    "INSERT INTO dealer_group (id, name, group_read_enabled, version, created_at, updated_at) "
    "VALUES (:id, 'Gruppe', false, 1, now(), now())"
)
_DEALERSHIP = text(
    "INSERT INTO dealership (id, dealer_group_id, legal_name, trade_name, franchise_type, address_street, "
    "address_house_number, address_postal_code, address_locality, address_canton, address_country, phone, uid, "
    "vat_liable, status, brand_primary_color, default_correspondence_language, entitlements_version, version, "
    "created_at, updated_at) "
    "VALUES (:id, :group, :legal, :trade, 'independent', :street, '1', :postal, :locality, :canton, 'CH', :phone, "
    ":uid, false, 'active', '#7C3AED', 'de', 1, 1, now(), now())"
)
_LOCATION = text(
    "INSERT INTO location (id, tenant_id, name, address_street, address_house_number, address_postal_code, "
    "address_locality, address_canton, address_country, version, created_at, updated_at) "
    "VALUES (:id, :tenant, :name, :street, :number, :postal, :locality, :canton, :country, 1, now(), now())"
)


def _rows_before(url: str) -> dict[str, uuid.UUID]:
    ids = {key: uuid.uuid4() for key in ("group", "with_sites", "fr_seat", "ne_seat", "zh_site", "ag_site")}
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(_GROUP, {"id": ids["group"]})

            def betrieb(key: str, legal: str, trade: str | None, street: str, postal: str, locality: str, canton: str):
                conn.execute(
                    _DEALERSHIP,
                    {
                        "id": ids[key], "group": ids["group"], "legal": legal, "trade": trade, "street": street,
                        "postal": postal, "locality": locality, "canton": canton, "phone": "+41441234567",
                        "uid": valid_uid(),
                    },
                )

            betrieb("with_sites", "Garage Zwei Standorte AG", None, "Bahnhofstrasse", "8001", "Zürich", "ZH")
            betrieb("fr_seat", "Garage Fribourg SA", None, "Route de Berne", "1700", "Fribourg", "FR")
            betrieb("ne_seat", "Neuchâtel Holding SA", "Garage du Lac", "Quai Ostervald", "2000", "Neuchâtel", "NE")
            # A site with a whole Zurich address, and one from before KAN-204
            # with a locality alone (no canton).
            conn.execute(
                _LOCATION,
                {
                    "id": ids["zh_site"], "tenant": ids["with_sites"], "name": "Zürich Nord", "street": "Hauptstrasse",
                    "number": "5", "postal": "8050", "locality": "Zürich", "canton": "ZH", "country": "CH",
                },
            )
            conn.execute(
                _LOCATION,
                {
                    "id": ids["ag_site"], "tenant": ids["with_sites"], "name": "Baden", "street": None,
                    "number": None, "postal": None, "locality": "Baden", "canton": None, "country": None,
                },
            )
    finally:
        engine.dispose()
    return ids


def test_the_backfills_give_every_site_its_defaults_and_every_betrieb_a_first_location(scratch_url):
    _alembic(scratch_url, "upgrade", _BEFORE)
    ids = _rows_before(scratch_url)

    _alembic(scratch_url, "upgrade", "heads")
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM holiday_calendar")).scalar_one() == 34
            assert conn.execute(text("SELECT count(*) FROM holiday")).scalar_one() == 302

            sites = conn.execute(
                text(
                    "SELECT l.id, l.tenant_id, l.name, l.code, l.location_types::text AS types, l.is_active, "
                    "l.address_street, l.address_house_number, l.address_postal_code, l.address_locality, "
                    "l.address_canton, l.address_country, l.phone, l.version, l.created_by, c.code AS calendar "
                    "FROM location l LEFT JOIN holiday_calendar c ON c.id = l.holiday_calendar_id"
                )
            ).all()
            by_id = {row.id: row for row in sites}
            by_tenant: dict[uuid.UUID, list] = {}
            for row in sites:
                by_tenant.setdefault(row.tenant_id, []).append(row)

            # Nothing is NULL that the model declares NOT NULL.
            assert all(row.types == "[]" and row.is_active is True for row in sites)

            # BACKFILL_LOCATIONS_SQL: the existing sites keep everything and get the canton's own calendar.
            assert by_id[ids["zh_site"]].calendar == "ZH"
            assert by_id[ids["ag_site"]].calendar is None  # no canton recorded
            assert by_id[ids["zh_site"]].code is None
            assert len(by_tenant[ids["with_sites"]]) == 2  # a Betrieb with sites gets no first location

            # FIRST_LOCATION_SQL: one location from the seat for each Betrieb without one.
            [fr] = by_tenant[ids["fr_seat"]]
            assert (fr.name, fr.address_street, fr.address_postal_code, fr.address_locality, fr.address_canton) == (
                "Garage Fribourg SA", "Route de Berne", "1700", "Fribourg", "FR",
            )  # no trade name → the legal name
            assert (fr.address_house_number, fr.address_country, fr.phone) == ("1", "CH", "+41441234567")
            assert fr.calendar is None  # Fribourg's holidays depend on the commune: the dealer picks
            assert (fr.code, fr.version, fr.created_by) == (None, 1, None)
            [ne] = by_tenant[ids["ne_seat"]]
            assert ne.name == "Garage du Lac"  # the trade name when there is one
            assert ne.calendar == "NE"  # the canton's main calendar, although Le Landeron has its own

            # The step is idempotent: a second run inserts nothing.
            before = conn.execute(text("SELECT count(*) FROM location")).scalar_one()
            conn.execute(text(_load_migration().FIRST_LOCATION_SQL))
            assert conn.execute(text("SELECT count(*) FROM location")).scalar_one() == before == 4
    finally:
        engine.dispose()

    _alembic(scratch_url, "downgrade", _BEFORE)
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT to_regclass('holiday_calendar')")).scalar() is None
            assert conn.execute(text("SELECT to_regclass('location_closure')")).scalar() is None
            columns = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'location'")
            ).scalars().all()
            assert "code" not in columns and "is_active" not in columns and "calendar_ref" in columns
            # A location is never deleted (A-RULE-1): the first locations stay as ordinary rows.
            assert conn.execute(text("SELECT count(*) FROM location")).scalar_one() == 4
    finally:
        engine.dispose()
