"""KAN-208 migration ec4559d84ad3, run for real on a throwaway Postgres
database: the seeded library is installed, every non-empty
`document_template.footer_text_*` becomes an `invoice.footer.carried_over`
dealer text of its Betrieb (one row per language, verified by query), the
four columns are gone — and the downgrade puts each footer back, creating the
template row where none exists any more."""

import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.dealership_support import valid_uid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BEFORE = "c4f1a9e2b7d3"

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
    name = "dms_kan208_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield sqlalchemy_url_for(base, name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _dealership(session: Session, name: str) -> uuid.UUID:
    group = DealerGroup(name=f"{name} group")
    session.add(group)
    session.flush()
    dealership = Dealership(
        dealer_group_id=group.id, legal_name=name, franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    session.add(dealership)
    session.flush()
    return dealership.id


def test_the_footers_move_into_the_library_and_come_back_on_downgrade(scratch_url):
    _alembic(scratch_url, "upgrade", _BEFORE)
    engine = create_engine(scratch_url)
    try:
        with Session(engine) as session:
            kept = _dealership(session, "Garage Behalten AG")
            dropped = _dealership(session, "Garage Ohne Vorlage AG")
            bare = _dealership(session, "Garage Leer AG")
            session.commit()
        with engine.begin() as conn:
            insert = text(
                "INSERT INTO document_template (id, dealership_id, header_note_de, footer_text_de, footer_text_fr, "
                "footer_text_en, version, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :d, :note, :de, :fr, :en, 1, now(), now())"
            )
            conn.execute(insert, {"d": kept, "note": "Kopf", "de": "Danke.", "fr": "Merci.", "en": "   "})
            conn.execute(insert, {"d": dropped, "note": None, "de": "Bis bald.", "fr": None, "en": None})
            conn.execute(insert, {"d": bare, "note": None, "de": None, "fr": None, "en": None})
    finally:
        engine.dispose()

    _alembic(scratch_url, "upgrade", "heads")
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            seeds = conn.execute(
                text("SELECT count(*), count(DISTINCT code) FROM standard_text WHERE source = 'platform_seed'")
            ).one()
            assert tuple(seeds) == (48, 12)
            carried = conn.execute(
                text(
                    "SELECT dealership_id, language, body, is_default, active, category, applies_to, slot, scope "
                    "FROM standard_text WHERE code = 'invoice.footer.carried_over' ORDER BY body"
                )
            ).all()
            assert [(r.dealership_id, r.language, r.body) for r in carried] == [
                (dropped, "de", "Bis bald."),
                (kept, "de", "Danke."),
                (kept, "fr", "Merci."),
            ]  # a blank footer moves nowhere; a Betrieb without one gets no row
            assert {(r.is_default, r.active, r.category, r.applies_to, r.slot, r.scope) for r in carried} == {
                (False, True, "document", "invoice", "footer", "dealership")
            }
            columns = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'document_template'")
            ).scalars().all()
            assert not [c for c in columns if c.startswith("footer_text")]
            assert "header_note_de" in columns
            conn.execute(text("DELETE FROM document_template WHERE dealership_id = :d"), {"d": dropped})
    finally:
        engine.dispose()

    _alembic(scratch_url, "downgrade", _BEFORE)
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT dealership_id, header_note_de, footer_text_de, footer_text_fr, footer_text_en "
                    "FROM document_template ORDER BY footer_text_de NULLS FIRST"
                )
            ).all()
            assert [tuple(r) for r in rows] == [
                (bare, None, None, None, None),
                (dropped, None, "Bis bald.", None, None),
                (kept, "Kopf", "Danke.", "Merci.", None),
            ]
            assert conn.execute(text("SELECT to_regclass('standard_text')")).scalar() is None
    finally:
        engine.dispose()
