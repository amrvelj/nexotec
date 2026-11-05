"""KAN-519 migration 7d3e9b2c4a18, run for real on a throwaway Postgres
database (Anto, 2026-11-03, option C): a footer KAN-208 carried over from
`document_template` that holds `{{` or `}}` is removed — in that language,
under all three carried-over codes — so the Betrieb's offers and contracts
print again (the seed's footer) instead of failing with 422
`standard_text_placeholder_unresolved`. Clean footers, other languages and a
Betrieb's own texts are untouched. Both paths: an environment whose data is
still to be migrated (KAN-208 and KAN-519 in one upgrade) and one where
KAN-208 ran already."""

import os
import pathlib
import subprocess
import sys
import uuid
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.standard_text import StandardTextAppliesTo, StandardTextCategory
from app.platform.services.standard_text_placeholders import render_body, target_for
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.document import generate_contract_document, generate_offer_document
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.dealership_support import valid_uid

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BEFORE_KAN208 = "444ddb84d8c9"  # the platform head before the footers were carried over
_BEFORE_KAN519 = "2b478e186431"  # the platform head before this migration
_CODES = ("invoice.footer.carried_over", "offer.footer.carried_over", "contract.footer.carried_over")

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _alembic(url: str, *args: str) -> str:
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
    return run.stdout


@pytest.fixture
def scratch_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan519_" + uuid.uuid4().hex[:12]
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


def _carried_over(conn) -> list[tuple[uuid.UUID, str, str, str]]:
    rows = conn.execute(
        text(
            "SELECT dealership_id, code, language, body FROM standard_text "
            "WHERE code IN :codes ORDER BY dealership_id, code, language"
        ).bindparams(bindparam("codes", expanding=True)),
        {"codes": list(_CODES)},
    ).all()
    return [tuple(r) for r in rows]


def _print_footers(session: Session, dealership_id: uuid.UUID, language: str) -> tuple[str | None, str | None]:
    offer = SalesOffer(
        tenant_id=dealership_id, offer_number=f"O-{uuid.uuid4().hex[:6]}", status=OfferStatus.OPEN,
        customer_label="Peter Beispiel", vehicle_label="VW Golf GTI", customer_language=language,
    )
    contract = SalesContract(
        tenant_id=dealership_id, contract_number=f"C-{uuid.uuid4().hex[:6]}", status=ContractStatus.PENDING,
        customer_label="Peter Beispiel", vehicle_source="manual", vehicle_label="VW Golf GTI",
        gross_price=Decimal("36000.00"), customer_language=language,
    )
    session.add_all([offer, contract])
    session.flush()
    return (
        generate_offer_document(session, offer=offer, actor_id=None).content_definition["footer_text"],
        generate_contract_document(session, contract=contract, actor_id=None).content_definition["footer_text"],
    )


def test_a_legacy_footer_with_a_stray_brace_is_removed_in_the_same_upgrade_that_carries_it_over(scratch_url):
    _alembic(scratch_url, "upgrade", _BEFORE_KAN208)
    engine = create_engine(scratch_url)
    try:
        with Session(engine) as session:
            braces = _dealership(session, "Garage Klammer AG")
            closing = _dealership(session, "Garage Schluss AG")
            clean = _dealership(session, "Garage Sauber AG")
            session.commit()
        with engine.begin() as conn:
            insert = text(
                "INSERT INTO document_template (id, dealership_id, footer_text_de, footer_text_fr, "
                "version, created_at, updated_at) VALUES (gen_random_uuid(), :d, :de, :fr, 1, now(), now())"
            )
            conn.execute(insert, {"d": braces, "de": "Danke {{ und auf Wiedersehen", "fr": "Merci."})
            conn.execute(insert, {"d": closing, "de": "Rabatt 5% }} gültig bis Ende Jahr", "fr": None})
            conn.execute(insert, {"d": clean, "de": "Danke.", "fr": None})
    finally:
        engine.dispose()

    output = _alembic(scratch_url, "upgrade", "heads")
    assert f"KAN-519 REPORT: removed offer.footer.carried_over (de) of Betrieb {braces}" in output
    assert f"KAN-519 REPORT: removed contract.footer.carried_over (de) of Betrieb {closing}" in output
    assert "KAN-519: 6 carried-over footer text(s) removed" in output

    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            remaining = _carried_over(conn)
        assert {(d, lang, body) for d, _, lang, body in remaining} == {(braces, "fr", "Merci."), (clean, "de", "Danke.")}
        assert len(remaining) == 6  # three codes for each footer that stays

        with Session(engine, expire_on_commit=False) as session:
            # Printing works again: the removed language falls back to the seed's footer …
            offer_de, contract_de = _print_footers(session, braces, "de")
            assert offer_de is not None and offer_de.startswith("Diese Offerte ist 30 Tage ab dem ")
            assert contract_de is not None and contract_de.startswith("Garage Klammer AG bearbeitet Ihre Personendaten")
            assert _print_footers(session, closing, "de")[0].startswith("Diese Offerte ist 30 Tage ab dem ")
            # … and the clean language and the clean Betrieb keep their own.
            assert _print_footers(session, braces, "fr") == ("Merci.", "Merci.")
            assert _print_footers(session, clean, "de") == ("Danke.", "Danke.")
            session.rollback()
    finally:
        engine.dispose()


def test_where_kan208_ran_already_only_the_carried_over_footers_with_a_brace_go(scratch_url):
    _alembic(scratch_url, "upgrade", "heads")
    _alembic(scratch_url, "downgrade", _BEFORE_KAN519)
    engine = create_engine(scratch_url)
    try:
        with Session(engine) as session:
            betrieb = _dealership(session, "Garage Bestand AG")
            session.commit()
        with engine.begin() as conn:
            insert = text(
                "INSERT INTO standard_text (id, dealership_id, location_id, scope, code, category, applies_to, "
                "slot, language, title, body, source, is_default, active, sort_order, template_approved, "
                "version, created_at, updated_at) VALUES (gen_random_uuid(), :d, NULL, 'dealership', :code, "
                "'document', :applies_to, 'footer', :language, 'Fusszeile', :body, 'dealer', :is_default, true, "
                "100, false, 1, now(), now())"
            )
            for code, applies_to, is_default in (
                ("offer.footer.carried_over", "offer", True),
                ("contract.footer.carried_over", "contract", True),
            ):
                conn.execute(insert, {"d": betrieb, "code": code, "applies_to": applies_to, "is_default": is_default,
                                      "language": "de", "body": "Bis {{bald}}"})
                conn.execute(insert, {"d": betrieb, "code": code, "applies_to": applies_to, "is_default": is_default,
                                      "language": "it", "body": "Grazie."})
            # A text the Betrieb wrote itself passed validate_body on save and is never touched.
            conn.execute(insert, {"d": betrieb, "code": "offer.footer.own", "applies_to": "offer",
                                  "is_default": False, "language": "de", "body": "{{ dealership.name }} dankt."})
    finally:
        engine.dispose()

    output = _alembic(scratch_url, "upgrade", "heads")
    assert "KAN-519: 2 carried-over footer text(s) removed" in output

    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT code, language, body FROM standard_text WHERE dealership_id = :d ORDER BY code, language"),
                {"d": betrieb},
            ).all()
        assert [tuple(r) for r in rows] == [
            ("contract.footer.carried_over", "it", "Grazie."),
            ("offer.footer.carried_over", "it", "Grazie."),
            ("offer.footer.own", "de", "{{ dealership.name }} dankt."),
        ]
        # The downgrade is a no-op: nothing comes back.
        _alembic(scratch_url, "downgrade", _BEFORE_KAN519)
        with engine.begin() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM standard_text WHERE dealership_id = :d"), {"d": betrieb}
            ).scalar() == 3
    finally:
        engine.dispose()


def test_a_carried_over_footer_with_a_stray_brace_cannot_print():
    """Why the rows go (the ticket's premise, pinned): the renderer reads a
    stray brace as a malformed placeholder and refuses the print, so the
    Betrieb's every offer in that language would fail."""

    target = target_for(StandardTextCategory.DOCUMENT, StandardTextAppliesTo.OFFER)
    assert target is not None
    for body in ("Danke {{ und auf Wiedersehen", "Rabatt 5% }} gültig bis Ende Jahr"):
        with pytest.raises(UnprocessableEntityError) as refused:
            render_body(body, target=target, values={})
        assert refused.value.details["reason"] == "standard_text_placeholder_unresolved"
