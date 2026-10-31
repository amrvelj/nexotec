"""KAN-208: the Sales documents print the Betrieb's standard texts — the
offer its introduction (`offer` / `intro`) and footer (`offer` / `footer`),
the contract its footer (`contract` / `footer`) — resolved through
app.platform.public.render_standard_text, frozen into the document. A token
Sales cannot fill fails the generation (A-RULE-4)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.errors import UnprocessableEntityError
from app.core.i18n import SwissLanguage, format_date_ch
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.standard_text import (
    StandardText,
    StandardTextAppliesTo,
    StandardTextScope,
    StandardTextSlot,
    StandardTextSource,
)
from app.platform.models.standard_text_delegation import StandardTextCategory
from app.platform.public import PLACEHOLDER_UNRESOLVED
from app.platform.services.standard_text_seed import SEED_TEXTS
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.document import DocumentOwnerType
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import count_documents
from app.sales.services.document import generate_contract_document, generate_offer_document
from tests.dealership_support import valid_uid

_SEEDS = {seed.code: seed for seed in SEED_TEXTS}


def _dealership(db_session) -> Dealership:
    group = DealerGroup(name="Gruppe")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id, legal_name="Garage AG", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
        vat_rate=Decimal("8.10"),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _offer(db_session, dealership, **overrides) -> SalesOffer:
    values = {
        "tenant_id": dealership.id,
        "offer_number": "O-000042",
        "status": OfferStatus.OPEN,
        "customer_label": "Peter Beispiel",
        "vehicle_label": "VW Golf GTI",
        "gross_price": Decimal("36000.00"),
    }
    values.update(overrides)
    offer = SalesOffer(**values)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)
    return offer


def _paragraphs(document) -> list[str]:
    return [b["text"] for b in document.content_definition["blocks"] if b["kind"] == "paragraph"]


def _override(db_session, dealership, *, code, slot, language, body):
    seed = db_session.scalar(
        select(StandardText).where(
            StandardText.dealership_id.is_(None), StandardText.code == code, StandardText.language == language
        )
    )
    row = StandardText(
        dealership_id=dealership.id, scope=StandardTextScope.DEALERSHIP, code=code,
        category=StandardTextCategory.DOCUMENT, applies_to=StandardTextAppliesTo.OFFER, slot=slot,
        language=language, title="Eigene", body=body, source=StandardTextSource.DEALER,
        overridden_from_id=seed.id, overridden_seed_version=seed.version, is_default=True, active=True,
        sort_order=10, version=1,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.mark.parametrize("language", ["de", "fr", "it", "en"])
def test_the_offer_prints_the_seeded_intro_and_footer_in_the_customers_language(
    db_session, standard_text_seeds, language
):
    from app.core.i18n import SwissLanguage

    dealership = _dealership(db_session)
    offer = _offer(db_session, dealership, customer_language=language)
    document = generate_offer_document(db_session, offer=offer, actor_id=None)

    lang = SwissLanguage(language)
    assert _paragraphs(document)[0] == _SEEDS["offer.intro.standard"].bodies[lang]
    footer = _SEEDS["offer.footer.validity"].bodies[lang].replace("{{document.date}}", format_date_ch(utcnow().date()))
    assert document.content_definition["footer_text"] == footer
    assert "{{" not in document.content_definition["footer_text"]


def test_the_betriebs_override_prints_and_an_earlier_document_stays_frozen(db_session, standard_text_seeds):
    from app.core.i18n import SwissLanguage

    dealership = _dealership(db_session)
    offer = _offer(db_session, dealership)
    first = generate_offer_document(db_session, offer=offer, actor_id=None)
    _override(
        db_session, dealership, code="offer.intro.standard", slot=StandardTextSlot.INTRO, language=SwissLanguage.DE,
        body="Grüezi! {{dealership.name}} offeriert Ihnen den {{vehicle.make_model}} ({{document.number}}).",
    )
    second = generate_offer_document(db_session, offer=offer, actor_id=None)

    assert _paragraphs(second)[0] == "Grüezi! Garage AG offeriert Ihnen den VW Golf GTI (O-000042)."
    db_session.refresh(first)
    assert _paragraphs(first)[0] == _SEEDS["offer.intro.standard"].bodies[SwissLanguage.DE]


def test_a_token_the_offer_cannot_fill_fails_the_generation_and_writes_nothing(db_session, standard_text_seeds):
    """An unassigned offer has no responsible seller: a footer naming
    {{user.full_name}} cannot render, so no document is generated — never a
    blank, never the raw token on the customer's paper."""

    from app.core.i18n import SwissLanguage

    dealership = _dealership(db_session)
    offer = _offer(db_session, dealership, owner_label=None)
    _override(
        db_session, dealership, code="offer.footer.validity", slot=StandardTextSlot.FOOTER,
        language=SwissLanguage.DE, body="Ihr Berater: {{user.full_name}}",
    )

    with pytest.raises(UnprocessableEntityError) as exc:
        generate_offer_document(db_session, offer=offer, actor_id=None)
    assert exc.value.details["reason"] == PLACEHOLDER_UNRESOLVED
    assert exc.value.details["placeholders"] == [{"token": "user.full_name", "problem": "unresolved"}]
    assert exc.value.details["code"] == "offer.footer.validity"
    assert count_documents(db_session, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id) == 0

    offer.owner_label = "Anna Beispiel"
    db_session.commit()
    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    assert document.content_definition["footer_text"] == "Ihr Berater: Anna Beispiel"


def test_without_any_text_for_the_slot_the_offer_prints_none(db_session):
    dealership = _dealership(db_session)
    offer = _offer(db_session, dealership)
    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    assert _paragraphs(document) == []
    assert document.content_definition["footer_text"] is None

    contract = _contract(db_session, dealership)
    assert generate_contract_document(db_session, contract=contract, actor_id=None).content_definition["footer_text"] is None


def _contract(db_session, dealership, **overrides) -> SalesContract:
    values = {
        "tenant_id": dealership.id,
        "contract_number": "C-000042",
        "status": ContractStatus.PENDING,
        "customer_label": "Peter Beispiel",
        "vehicle_source": "manual",
        "vehicle_label": "VW Golf GTI",
        "gross_price": Decimal("36000.00"),
    }
    values.update(overrides)
    contract = SalesContract(**values)
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)
    return contract


def _own_default_footer(db_session, dealership, *, applies_to, code, body, language=SwissLanguage.DE):
    row = StandardText(
        dealership_id=dealership.id, scope=StandardTextScope.DEALERSHIP, code=code,
        category=StandardTextCategory.DOCUMENT, applies_to=applies_to, slot=StandardTextSlot.FOOTER,
        language=language, title="Fusszeile", body=body, source=StandardTextSource.DEALER,
        is_default=True, active=True, sort_order=100, version=1,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_the_contract_prints_its_footer_through_the_resolver(db_session, standard_text_seeds):
    """KAN-208 (review): the contract consumes `contract` / `footer` too —
    the seed default (the data-protection notice) until the Betrieb has its
    own default, then that one: the shape a footer carried over from the
    document template takes (`contract.footer.carried_over`)."""

    dealership = _dealership(db_session)
    seeded = generate_contract_document(db_session, contract=_contract(db_session, dealership), actor_id=None)
    assert seeded.content_definition["footer_text"] == _SEEDS["contract.footer.data_protection"].bodies[
        SwissLanguage.DE
    ].replace("{{dealership.name}}", "Garage AG")

    _own_default_footer(
        db_session, dealership, applies_to=StandardTextAppliesTo.CONTRACT, code="contract.footer.carried_over",
        body="Vielen Dank für Ihr Vertrauen. Vertrag {{document.number}}.",
    )
    carried = generate_contract_document(
        db_session, contract=_contract(db_session, dealership, contract_number="C-000043"), actor_id=None
    )
    assert carried.content_definition["footer_text"] == "Vielen Dank für Ihr Vertrauen. Vertrag C-000043."


def test_a_carried_over_footer_prints_on_the_offer_and_the_contract(db_session, standard_text_seeds):
    """The rows the migration writes for a Betrieb that had a template footer
    — its default offer and contract footer — print on both documents, ahead
    of the seed defaults; a language without one gets the seed's."""

    dealership = _dealership(db_session)
    for applies_to, code in (
        (StandardTextAppliesTo.OFFER, "offer.footer.carried_over"),
        (StandardTextAppliesTo.CONTRACT, "contract.footer.carried_over"),
    ):
        _own_default_footer(db_session, dealership, applies_to=applies_to, code=code, body="Danke für Ihren Einkauf.")

    offer = generate_offer_document(db_session, offer=_offer(db_session, dealership), actor_id=None)
    contract = generate_contract_document(db_session, contract=_contract(db_session, dealership), actor_id=None)
    assert offer.content_definition["footer_text"] == "Danke für Ihren Einkauf."
    assert contract.content_definition["footer_text"] == "Danke für Ihren Einkauf."

    french = generate_offer_document(
        db_session, offer=_offer(db_session, dealership, offer_number="O-000043", customer_language="fr"), actor_id=None
    )
    assert french.content_definition["footer_text"].startswith("Cette offre est valable 30 jours")


def test_the_api_says_why_a_generation_failed(client, engine, standard_text_seeds):
    from sqlalchemy.orm import sessionmaker

    from app.core.auth import AccessRole
    from app.core.i18n import SwissLanguage
    from tests.membership_support import bearer, create_dealership, token

    home = create_dealership(client)
    seller = bearer(token(AccessRole.SALES, tenant_id=home))
    offer = client.post("/v1/sales/offers", headers=seller).json()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        dealership = session.get(Dealership, uuid.UUID(home))
        _override(
            session, dealership, code="offer.intro.standard", slot=StandardTextSlot.INTRO,
            language=SwissLanguage.DE, body="Für den {{vehicle.make_model}}",
        )
    finally:
        session.close()

    # A fresh offer has no car yet: the intro cannot name it.
    response = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=seller)
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == PLACEHOLDER_UNRESOLVED
    assert details["placeholders"] == [{"token": "vehicle.make_model", "problem": "unresolved"}]
    assert client.get(f"/v1/sales/offers/{offer['id']}/documents", headers=seller).json()["items"] == []
