"""WP-8 PR-7: sales_document generation (append-only, version-on-
generation-never-on-edit) and the margin-never-on-the-document guarantee.
"""

import uuid
from decimal import Decimal

from app.core.auth import AccessRole, create_access_token
from app.core.i18n import SwissLanguage
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.document import DocumentOwnerType
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.document import (
    build_contract_content,
    build_offer_content,
    count_documents,
    generate_contract_document,
    generate_offer_document,
    list_documents,
)

# Sentinel figures that must NEVER surface on a customer-facing document —
# the seller-only margin panel is a separate surface entirely (ADR-063).
_MARGIN_SENTINEL = Decimal("999999.00")
_COST_BASIS_SENTINEL = Decimal("888888.00")
_TRADE_IN_PURCHASE_SENTINEL = Decimal("777777.00")


def _bare_offer(**overrides) -> SalesOffer:
    defaults = {
        "tenant_id": uuid.uuid4(),
        "offer_number": "O-000001",
        "status": OfferStatus.OPEN,
        "customer_label": "Peter Beispiel",
        "vehicle_label": "VW Golf GTI",
        "base_price": Decimal("38000.00"),
        "list_price": Decimal("38000.00"),
        "gross_price": Decimal("36000.00"),
        "discount_amount": Decimal("2000.00"),
        "margin": _MARGIN_SENTINEL,
        "cost_basis": _COST_BASIS_SENTINEL,
        "trade_in_purchase_price": _TRADE_IN_PURCHASE_SENTINEL,
    }
    defaults.update(overrides)
    return SalesOffer(**defaults)


def _bare_contract(**overrides) -> SalesContract:
    defaults = {
        "tenant_id": uuid.uuid4(),
        "contract_number": "C-000001",
        "status": ContractStatus.PENDING,
        "customer_label": "Peter Beispiel",
        "vehicle_label": "VW Golf GTI",
        "base_price": Decimal("38000.00"),
        "list_price": Decimal("38000.00"),
        "gross_price": Decimal("36000.00"),
        "discount_amount": Decimal("2000.00"),
        "margin": _MARGIN_SENTINEL,
        "trade_in_purchase_price": _TRADE_IN_PURCHASE_SENTINEL,
    }
    defaults.update(overrides)
    return SalesContract(**defaults)


def _dump_text(content) -> str:
    return content.model_dump_json()


def _make_dealership(db_session, *, tenant_id: uuid.UUID | None = None, vat_rate: Decimal = Decimal("8.10")) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=tenant_id or uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG", dealer_license_number="ZH-1",
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse",
        address_house_number="1", address_postal_code="8001", address_locality="Zürich", address_canton="ZH",
        phone="+41441234567", tax_id="CHE-123.456.789", vat_rate=vat_rate,
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


# --- the structural guarantee (ADR-063) -------------------------------------


def test_offer_document_never_carries_margin_cost_or_trade_in_purchase_price():
    offer = _bare_offer()
    dumped = _dump_text(build_offer_content(offer, language=SwissLanguage.DE, vat_rate=Decimal("8.10")))
    assert str(_MARGIN_SENTINEL) not in dumped
    assert str(_COST_BASIS_SENTINEL) not in dumped
    assert str(_TRADE_IN_PURCHASE_SENTINEL) not in dumped
    assert "margin" not in dumped.lower()
    assert "cost_basis" not in dumped.lower() and "costbasis" not in dumped.lower()


def test_contract_document_never_carries_margin_or_trade_in_purchase_price():
    contract = _bare_contract()
    dumped = _dump_text(build_contract_content(contract, language=SwissLanguage.DE, vat_rate=Decimal("8.10")))
    assert str(_MARGIN_SENTINEL) not in dumped
    assert str(_TRADE_IN_PURCHASE_SENTINEL) not in dumped
    assert "margin" not in dumped.lower()


def test_offer_document_does_carry_the_customer_facing_price_build_up():
    offer = _bare_offer()
    dumped = _dump_text(build_offer_content(offer, language=SwissLanguage.DE, vat_rate=Decimal("8.10")))
    assert str(offer.gross_price) in dumped
    assert offer.vehicle_label in dumped
    assert offer.customer_label in dumped


# --- KAN-23: correspondence language and the single VAT line -----------------


def _line_items_lines(content) -> list:
    from app.platform.public import LineItemsBlock

    for block in content.blocks:
        if isinstance(block, LineItemsBlock):
            return block.lines
    raise AssertionError("no LineItemsBlock in this document")


def test_offer_renders_in_all_four_languages_and_the_body_changes_not_just_the_letterhead():
    """WP-6b's own exit criterion: a document rendered in each of the four
    languages must differ in its BODY content, not only in whatever the
    render layer adds around it (the letterhead is a separate concern,
    tested in app/platform's own suite) — this asserts the
    ContentDefinition build_offer_content itself produces, which is what
    gets frozen.
    """

    offer = _bare_offer()
    rendered = {
        language: _dump_text(build_offer_content(offer, language=language, vat_rate=Decimal("8.10")))
        for language in SwissLanguage
    }
    assert len(set(rendered.values())) == 4, "all four languages must produce distinct body content"

    # Spot-check the actual label text, not just "the strings differ somehow".
    assert "Grundpreis" in rendered[SwissLanguage.DE]
    assert "Prix de base" in rendered[SwissLanguage.FR]
    assert "Prezzo base" in rendered[SwissLanguage.IT]
    assert "Base price" in rendered[SwissLanguage.EN]
    # The greeting paragraph too — proves the body changed, not only the
    # price-build-up labels.
    assert "Wir freuen uns" in rendered[SwissLanguage.DE]
    assert "plaisir de vous proposer" in rendered[SwissLanguage.FR]


def test_a_french_offer_never_shows_a_german_label():
    """Read for text-expansion too (French labels run longer than German
    — "Total des options" vs "Optionen total", "Montant à payer" vs "Zu
    bezahlen") — visually eyeballed against the rendered PDF layout
    separately (WP-6b's own exit criterion); this asserts the content
    itself carries no German leftover, which a layout review alone
    wouldn't catch.
    """

    offer = _bare_offer()
    content = build_offer_content(offer, language=SwissLanguage.FR, vat_rate=Decimal("8.10"))
    dumped = _dump_text(content)
    for german_label in ("Grundpreis", "Listenpreis", "Verkaufspreis", "Zu bezahlen", "Rabatt"):
        assert german_label not in dumped


def test_exactly_one_vat_line_on_the_offer():
    """Asserted by COUNT, not by presence — a test that only checks a VAT
    line exists would pass with three (the ticket's own warning).
    """

    offer = _bare_offer()
    content = build_offer_content(offer, language=SwissLanguage.DE, vat_rate=Decimal("8.10"))
    lines = _line_items_lines(content)
    vat_lines = [line for line in lines if "MWST" in line.label or "VAT" in line.label]
    assert len(vat_lines) == 1


def test_exactly_one_vat_line_on_the_contract():
    contract = _bare_contract()
    content = build_contract_content(contract, language=SwissLanguage.DE, vat_rate=Decimal("8.10"))
    lines = _line_items_lines(content)
    vat_lines = [line for line in lines if "MWST" in line.label]
    assert len(vat_lines) == 1


def test_vat_line_is_the_reverse_inclusive_component_of_the_gross_price_not_an_addition():
    """The gross price already INCLUDES VAT (ADR-057) — the VAT line is a
    component OF payable, computed with the same reverse-inclusive
    formula the fiktiver Vorsteuerabzug uses
    (app.inventory.services.purchase::_compute_notional_input_tax), never
    a separate charge added on top. Sign/magnitude asserted explicitly,
    not just "a line exists."
    """

    offer = _bare_offer(payable=Decimal("36000.00"))
    content = build_offer_content(offer, language=SwissLanguage.DE, vat_rate=Decimal("8.10"))
    lines = _line_items_lines(content)
    vat_line = next(line for line in lines if "MWST" in line.label)

    expected = (Decimal("36000.00") * Decimal("8.10") / Decimal("108.10")).quantize(Decimal("0.01"))
    assert vat_line.amount == expected
    assert vat_line.amount > 0
    assert vat_line.amount < offer.payable  # the VAT component is smaller than the price it's a component of
    assert "8.1" in vat_line.label  # the rate itself is stated on the one line


def test_no_vat_line_without_a_configured_rate():
    """No guessed rate, ever — a dealership that hasn't configured
    vat_rate gets no VAT line at all, not a wrong one.
    """

    offer = _bare_offer()
    content = build_offer_content(offer, language=SwissLanguage.DE, vat_rate=None)
    lines = _line_items_lines(content)
    assert not any("MWST" in line.label for line in lines)


def test_generated_document_freezes_the_dealerships_vat_rate_at_generation_time(db_session):
    dealership = _make_dealership(db_session, vat_rate=Decimal("7.70"))
    offer = _bare_offer(tenant_id=dealership.id)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)

    document = generate_offer_document(db_session, offer=offer, actor_id=None)

    lines = document.content_definition["blocks"]
    line_items = next(b["lines"] for b in lines if b.get("kind") == "line_items")
    vat_line = next(line for line in line_items if "MWST" in line["label"])
    assert "7.7" in vat_line["label"]


# --- append-only, version-on-generation-never-on-edit ------------------------


def test_generate_offer_document_allocates_sequential_versions(db_session):
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)

    first = generate_offer_document(db_session, offer=offer, actor_id=None)
    second = generate_offer_document(db_session, offer=offer, actor_id=None)

    assert first.version == 1
    assert second.version == 2
    assert first.id != second.id  # a NEW row, the old one is never edited

    documents = list_documents(
        db_session, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id
    )
    assert [d.version for d in documents] == [2, 1]  # newest first
    assert count_documents(
        db_session, tenant_id=offer.tenant_id, owner_type=DocumentOwnerType.OFFER, owner_id=offer.id
    ) == 2


def test_generate_contract_document_is_independent_of_the_offer_sequence(db_session):
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)
    generate_offer_document(db_session, offer=offer, actor_id=None)  # offer's own v1

    contract = _bare_contract(tenant_id=offer.tenant_id, offer_id=offer.id, offer_number=offer.offer_number)
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)

    contract_document = generate_contract_document(db_session, contract=contract, actor_id=None)
    assert contract_document.version == 1  # its own sequence, not the offer's


def test_document_correspondence_language_defaults_when_customer_has_none(db_session):
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id, customer_language=None)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)

    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    assert document.correspondence_language == "de"


def test_document_correspondence_language_follows_the_customer_never_the_actor(db_session):
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id, customer_language="fr")
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)

    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    assert document.correspondence_language == "fr"


# --- API surface ---------------------------------------------------------------


def _token(role: AccessRole | None = None, tenant_id: uuid.UUID | None = None) -> str:
    tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({role}) if role else frozenset(),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _create_dealer(client) -> str:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": _VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_generate_and_list_offer_documents_via_api(client):
    dealer_id = _create_dealer(client)
    token = _token(role=AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    generated = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(token))
    assert generated.status_code == 201, generated.text
    assert generated.json()["version"] == 1

    listed = client.get(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1


def test_document_pdf_download_cross_tenant_is_404(client):
    owner_dealer_id = _create_dealer(client)
    other_dealer_id = _create_dealer(client)
    owner = _token(role=AccessRole.SALES, tenant_id=uuid.UUID(owner_dealer_id))
    other = _token(role=AccessRole.SALES, tenant_id=uuid.UUID(other_dealer_id))
    offer = client.post("/v1/sales/offers", headers=_bearer(owner)).json()
    document = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(owner)).json()

    response = client.get(f"/v1/sales/documents/{document['id']}/pdf", headers=_bearer(other))
    assert response.status_code == 404, response.text


def test_document_pdf_download_produces_a_real_pdf(client):
    """The one end-to-end test that exercises the real WeasyPrint render
    path (mirrors test_document_render.py's own single end-to-end case) —
    everything else in this file works against the pure ContentDefinition/
    ORM layer so it runs without the system Pango/GLib libraries."""

    dealer_id = _create_dealer(client)
    token = _token(role=AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    document = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(token)).json()

    response = client.get(f"/v1/sales/documents/{document['id']}/pdf", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
