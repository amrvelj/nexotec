"""KAN-206 on Sales' documents: the responsible seller's signature image on
offers AND contracts, read when the document is printed (Anto, 2026-11-12,
Q2 A), and the draft watermark on an offer's document while the offer is a
draft. The price build-up is untouched (ADR-057)."""

import base64
import uuid
from unittest.mock import patch

import pytest

from app.core.i18n import SwissLanguage
from app.core.object_store import LocalDirectoryObjectStore
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.schemas.document_content import DocumentWatermark, SignatureBlock
from app.sales.models.offer import OfferStatus
from app.sales.services.document import (
    build_contract_content,
    build_offer_content,
    generate_contract_document,
    generate_offer_document,
    render_document_pdf,
)
from tests.test_sales_documents import _bare_contract, _bare_offer, _make_dealership


def _signature(content) -> SignatureBlock:
    blocks = [block for block in content.blocks if isinstance(block, SignatureBlock)]
    assert len(blocks) == 1
    return blocks[0]


def test_the_offer_ends_in_the_responsible_sellers_signature_line():
    seller_id = uuid.uuid4()
    content = build_offer_content(
        _bare_offer(owner_user_id=seller_id, owner_label="Vera Verkauf"), language=SwissLanguage.DE, vat_rate=None
    )
    signature = _signature(content)
    assert content.blocks[-1] is signature
    assert signature.labels == ["Vera Verkauf"]
    assert signature.signer_user_ids == [seller_id]


def test_an_unassigned_offer_prints_a_bare_seller_line():
    content = build_offer_content(_bare_offer(), language=SwissLanguage.FR, vat_rate=None)
    assert _signature(content).labels == ["Vendeur"]
    assert _signature(content).signer_user_ids == [None]


@pytest.mark.parametrize(
    ("language", "labels"),
    [
        (SwissLanguage.DE, ["Käufer", "Verkäufer"]),
        (SwissLanguage.FR, ["Acheteur", "Vendeur"]),
        (SwissLanguage.IT, ["Acquirente", "Venditore"]),
        (SwissLanguage.EN, ["Buyer", "Seller"]),
    ],
)
def test_the_contract_has_the_buyers_line_and_the_sellers_with_their_image(language, labels):
    seller_id = uuid.uuid4()
    content = build_contract_content(_bare_contract(owner_user_id=seller_id), language=language, vat_rate=None)
    signature = _signature(content)
    assert signature.labels == labels
    assert signature.signer_user_ids == [None, seller_id]


def test_the_price_build_up_is_unchanged_by_the_signature_block():
    offer = _bare_offer(owner_user_id=uuid.uuid4())
    content = build_offer_content(offer, language=SwissLanguage.DE, vat_rate=None)
    assert [block.kind for block in content.blocks] == ["key_value", "line_items", "signature"]


# --- printed: the image read at render time, the draft watermark -----------------------


def _seller(db_session, tenant_id: uuid.UUID, key: str) -> User:
    user = User(
        tenant_id=tenant_id, first_name="Vera", last_name="Verkauf", email=f"{uuid.uuid4().hex}@example.test",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()), signature_image_ref=key,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _printed_html(db_session, document, store) -> str:
    with (
        patch("app.platform.services.document_render.get_object_store", return_value=store),
        patch("app.platform.services.document_render.write_pdf", return_value=b"%PDF-stub") as write_pdf,
    ):
        render_document_pdf(db_session, document=document, dealership_id=document.tenant_id)
    (html_string,) = write_pdf.call_args.args
    return html_string


@pytest.mark.parametrize("kind", ["offer", "contract"])
def test_the_sellers_current_signature_image_prints_on_offers_and_contracts(db_session, tmp_path, kind):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id, "sig/vera")
    store.put("sig/vera", b"\x89PNG-vera-1", content_type="image/png")
    if kind == "offer":
        record = _bare_offer(tenant_id=dealership.id, owner_user_id=seller.id, owner_label="Vera Verkauf")
        db_session.add(record)
        db_session.commit()
        document = generate_offer_document(db_session, offer=record, actor_id=None)
    else:
        record = _bare_contract(tenant_id=dealership.id, owner_user_id=seller.id, owner_label="Vera Verkauf")
        db_session.add(record)
        db_session.commit()
        document = generate_contract_document(db_session, contract=record, actor_id=None)

    assert base64.b64encode(b"\x89PNG-vera-1").decode() in _printed_html(db_session, document, store)
    # A reprint after the seller replaced the image prints the new one.
    store.put("sig/vera", b"\x89PNG-vera-2", content_type="image/png")
    reprint = _printed_html(db_session, document, store)
    assert base64.b64encode(b"\x89PNG-vera-2").decode() in reprint
    assert base64.b64encode(b"\x89PNG-vera-1").decode() not in reprint


def test_a_seller_without_an_image_prints_the_bare_line(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id, owner_user_id=uuid.uuid4(), owner_label="Vera Verkauf")
    db_session.add(offer)
    db_session.commit()
    document = generate_offer_document(db_session, offer=offer, actor_id=None)
    html_string = _printed_html(db_session, document, store)
    assert 'class="doc-sign-line"' in html_string
    assert '<div class="doc-sign-image"></div>' in html_string


def _render_kwargs(db_session, document) -> dict:
    with patch("app.sales.services.document.render_document", return_value=b"%PDF-stub") as render:
        render_document_pdf(db_session, document=document, dealership_id=document.tenant_id)
    return render.call_args.kwargs


def test_an_offer_document_is_a_draft_while_its_offer_is(db_session):
    dealership = _make_dealership(db_session)
    offer = _bare_offer(tenant_id=dealership.id, status=OfferStatus.DRAFT)
    db_session.add(offer)
    db_session.commit()
    document = generate_offer_document(db_session, offer=offer, actor_id=None)

    assert _render_kwargs(db_session, document)["watermark"] is DocumentWatermark.DRAFT
    offer.status = OfferStatus.OPEN  # finalised: the same document now prints clean
    db_session.commit()
    assert _render_kwargs(db_session, document)["watermark"] is None


def test_a_contract_document_is_never_a_draft(db_session):
    dealership = _make_dealership(db_session)
    contract = _bare_contract(tenant_id=dealership.id)
    db_session.add(contract)
    db_session.commit()
    document = generate_contract_document(db_session, contract=contract, actor_id=None)
    assert _render_kwargs(db_session, document)["watermark"] is None
