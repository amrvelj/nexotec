"""WP-8 PR-7: sales_document generation (append-only, version-on-
generation-never-on-edit) and the margin-never-on-the-document guarantee.
"""

import uuid
from decimal import Decimal

from app.core.auth import AccessRole, create_access_token
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


# --- the structural guarantee (ADR-063) -------------------------------------


def test_offer_document_never_carries_margin_cost_or_trade_in_purchase_price():
    offer = _bare_offer()
    dumped = _dump_text(build_offer_content(offer))
    assert str(_MARGIN_SENTINEL) not in dumped
    assert str(_COST_BASIS_SENTINEL) not in dumped
    assert str(_TRADE_IN_PURCHASE_SENTINEL) not in dumped
    assert "margin" not in dumped.lower()
    assert "cost_basis" not in dumped.lower() and "costbasis" not in dumped.lower()


def test_contract_document_never_carries_margin_or_trade_in_purchase_price():
    contract = _bare_contract()
    dumped = _dump_text(build_contract_content(contract))
    assert str(_MARGIN_SENTINEL) not in dumped
    assert str(_TRADE_IN_PURCHASE_SENTINEL) not in dumped
    assert "margin" not in dumped.lower()


def test_offer_document_does_carry_the_customer_facing_price_build_up():
    offer = _bare_offer()
    dumped = _dump_text(build_offer_content(offer))
    assert str(offer.gross_price) in dumped
    assert offer.vehicle_label in dumped
    assert offer.customer_label in dumped


# --- append-only, version-on-generation-never-on-edit ------------------------


def test_generate_offer_document_allocates_sequential_versions(db_session):
    offer = _bare_offer()
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
    offer = _bare_offer()
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
    offer = _bare_offer(customer_language=None)
    db_session.add(offer)
    db_session.commit()
    db_session.refresh(offer)

    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    assert document.correspondence_language == "de"


def test_document_correspondence_language_follows_the_customer_never_the_actor(db_session):
    offer = _bare_offer(customer_language="fr")
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
    token = _token(role=AccessRole.SALES)
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()

    generated = client.post(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(token))
    assert generated.status_code == 201, generated.text
    assert generated.json()["version"] == 1

    listed = client.get(f"/v1/sales/offers/{offer['id']}/documents", headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1


def test_document_pdf_download_cross_tenant_is_404(client):
    owner = _token(role=AccessRole.SALES)
    other = _token(role=AccessRole.SALES)
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
