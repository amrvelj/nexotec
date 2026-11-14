"""KAN-208 migration ec4559d84ad3, run for real on a throwaway Postgres
database: the seeded library is installed, every non-empty
`document_template.footer_text_*` becomes an `invoice.footer.carried_over`
dealer text of its Betrieb and its **default** offer and contract footer (one
row per language each, verified by query), so an offer and a contract
generated after the upgrade print the footer they printed before; the four
columns are gone — and the downgrade puts each footer back, creating the
template row where none exists any more."""

import os
import pathlib
import subprocess
import sys
import uuid
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.orm import Session

from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.document import generate_contract_document, generate_offer_document
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.dealership_support import valid_uid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BEFORE = "444ddb84d8c9"  # the platform head before KAN-208 once the batch is chained (KAN-205)

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
    # A Core insert names only the columns given (and those with a Python
    # default), so a dealership column added after this revision (KAN-206's
    # `email`, `website`) is not sent to a database that predates it.
    return session.execute(
        insert(Dealership)
        .values(
            dealer_group_id=group.id, legal_name=name, franchise_type=FranchiseType.INDEPENDENT,
            address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
            address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
        )
        .returning(Dealership.id)
    ).scalar_one()


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
            # The same footer is the Betrieb's default offer and contract footer,
            # one row per language it had, a dealer text of its own.
            for code, applies_to in (("offer.footer.carried_over", "offer"), ("contract.footer.carried_over", "contract")):
                rows = conn.execute(
                    text(
                        "SELECT dealership_id, language, body, is_default, active, source, overridden_from_id, "
                        "category, applies_to, slot, scope FROM standard_text WHERE code = :code ORDER BY body"
                    ),
                    {"code": code},
                ).all()
                assert [(r.dealership_id, r.language, r.body) for r in rows] == [
                    (dropped, "de", "Bis bald."),
                    (kept, "de", "Danke."),
                    (kept, "fr", "Merci."),
                ], code
                assert {
                    (r.is_default, r.active, r.source, r.overridden_from_id, r.category, r.applies_to, r.slot, r.scope)
                    for r in rows
                } == {(True, True, "dealer", None, "document", applies_to, "footer", "dealership")}, code
            own_rows = conn.execute(text("SELECT count(*) FROM standard_text WHERE dealership_id = :d"), {"d": bare})
            assert own_rows.scalar() == 0  # a Betrieb without a footer gets no row
            columns = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'document_template'")
            ).scalars().all()
            assert not [c for c in columns if c.startswith("footer_text")]
            assert "header_note_de" in columns
            conn.execute(text("DELETE FROM document_template WHERE dealership_id = :d"), {"d": dropped})

        # The documents themselves, generated on the migrated rows: the footer
        # an offer and a contract printed from the template is the footer they
        # print now. A language without one (EN here) and a Betrieb without one
        # get the seed default — the ticket's stated change.
        with Session(engine, expire_on_commit=False) as session:

            def footers(dealership_id: uuid.UUID, language: str) -> tuple[str | None, str | None]:
                offer = SalesOffer(
                    tenant_id=dealership_id, offer_number=f"O-{uuid.uuid4().hex[:6]}", status=OfferStatus.OPEN,
                    customer_label="Peter Beispiel", vehicle_label="VW Golf GTI", customer_language=language,
                )
                contract = SalesContract(
                    tenant_id=dealership_id, contract_number=f"C-{uuid.uuid4().hex[:6]}",
                    status=ContractStatus.PENDING, customer_label="Peter Beispiel", vehicle_source="manual",
                    vehicle_label="VW Golf GTI", gross_price=Decimal("36000.00"), customer_language=language,
                )
                session.add_all([offer, contract])
                session.flush()
                offer_document = generate_offer_document(session, offer=offer, actor_id=None)
                contract_document = generate_contract_document(session, contract=contract, actor_id=None)
                return (
                    offer_document.content_definition["footer_text"],
                    contract_document.content_definition["footer_text"],
                )

            assert footers(kept, "de") == ("Danke.", "Danke.")
            assert footers(kept, "fr") == ("Merci.", "Merci.")
            assert footers(dropped, "de") == ("Bis bald.", "Bis bald.")
            offer_seed, contract_seed = footers(bare, "de")
            assert offer_seed is not None and offer_seed.startswith("Diese Offerte ist 30 Tage ab dem ")
            assert contract_seed is not None and contract_seed.startswith("Garage Leer AG bearbeitet Ihre Personendaten")
            offer_en, contract_en = footers(kept, "en")
            assert offer_en is not None and offer_en.startswith("This offer is valid for 30 days")
            assert contract_en is not None and contract_en.startswith("Garage Behalten AG processes your personal data")
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
