"""KAN-217 PR-4 (ADR-192): secure document links — the link an SMS or a
WhatsApp send carries in place of the PDF.

Platform issues and serves the link; Sales renders the document through the
source it registers from the composition root. The flow spans both, hence
cross_context.
"""

import datetime as dt
import hashlib
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.redact import is_secret_field
from app.platform.documents.models.document_link import LINK_VALID_DAYS, OPEN_WINDOW, OPENS_PER_WINDOW, DocumentLink
from app.platform.documents.services import document_links
from app.platform.documents.services.message_types import MessageTypeRegistrationError
from app.platform.organisation.models.dealership import DealerGroup, Dealership, DealershipStatus, FranchiseType
from app.platform.public import RenderedDocument, issue_document_link, register_document_source
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.document import generate_contract_document, generate_offer_document
from tests.dealership_support import valid_uid


def _dealership(db, name: str = "Garage AG") -> Dealership:
    group = DealerGroup(name=f"{name} group")
    db.add(group)
    db.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name=name,
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        uid=valid_uid(),
        vat_rate=Decimal("8.10"),
    )
    db.add(dealership)
    db.commit()
    return dealership


def _offer_document(db, dealership: Dealership, number: str = "O-2026/000001"):
    offer = SalesOffer(
        tenant_id=dealership.id,
        offer_number=number,
        status=OfferStatus.OPEN,
        customer_label="Peter Beispiel",
        vehicle_label="VW Golf GTI",
        base_price=Decimal("38000.00"),
        list_price=Decimal("38000.00"),
        gross_price=Decimal("36000.00"),
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return generate_offer_document(db, offer=offer, actor_id=None)


def _issue(db, dealership: Dealership, document_id: uuid.UUID, message_type: str = "offer", **kwargs):
    issued = issue_document_link(
        db, tenant_id=dealership.id, message_type=message_type, document_id=document_id, actor_id=None, **kwargs
    )
    db.commit()
    return issued


def _open(client, token: str):
    return client.get("/v1/document-links/current", headers={"X-Document-Link": token})


# --- the token is a password ---------------------------------------------------


def test_only_the_tokens_sha256_is_stored(db_session):
    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)

    issued = _issue(db_session, dealership, document.id)

    row = db_session.scalar(select(DocumentLink).where(DocumentLink.id == issued.link_id))
    assert row.token_hash == hashlib.sha256(issued.token.encode()).hexdigest()
    stored = " ".join(str(getattr(row, column.key)) for column in DocumentLink.__table__.columns)
    assert issued.token not in stored
    # The token rides in the fragment, which no browser sends to a server.
    assert issued.url.endswith("/d#" + issued.token)
    # The page opens in the customer's language; that may be a query, the token never.
    in_french = issue_document_link(
        db_session, tenant_id=dealership.id, message_type="offer", document_id=document.id, actor_id=None, language="fr"
    )
    assert in_french.url.endswith("/d?lang=fr#" + in_french.token)
    assert row.expires_at - utcnow() > dt.timedelta(days=LINK_VALID_DAYS) - dt.timedelta(minutes=1)


def test_two_links_never_share_a_token(db_session):
    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    assert _issue(db_session, dealership, document.id).token != _issue(db_session, dealership, document.id).token


# --- opening ----------------------------------------------------------------------


def test_a_link_serves_the_offer_pdf_without_a_sign_in(client, db_session):
    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id)

    response = _open(client, issued.token)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    # The range's «/» cannot be in a file name.
    assert response.headers["content-disposition"] == 'inline; filename="O-2026-000001-v1.pdf"'
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in response.headers["x-robots-tag"]
    db_session.expire_all()
    row = db_session.scalar(select(DocumentLink).where(DocumentLink.id == issued.link_id))
    assert row.open_count == 1
    assert row.first_opened_at is not None and row.last_opened_at is not None


def test_a_link_serves_the_contract_pdf(client, db_session):
    dealership = _dealership(db_session)
    contract = SalesContract(
        tenant_id=dealership.id,
        contract_number="C-000007",
        status=ContractStatus.PENDING,
        customer_label="Peter Beispiel",
        vehicle_source="manual",
        vehicle_label="VW Golf GTI",
        base_price=Decimal("38000.00"),
        list_price=Decimal("38000.00"),
        gross_price=Decimal("36000.00"),
    )
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)
    document = generate_contract_document(db_session, contract=contract, actor_id=None)
    issued = _issue(db_session, dealership, document.id, message_type="sales_contract")

    response = _open(client, issued.token)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'inline; filename="C-000007-v1.pdf"'


@pytest.mark.parametrize("token", ["", "nope", "A" * 43, "A" * 42 + "!", "A" * 200])
def test_an_unknown_missing_or_malformed_token_is_404(client, token):
    assert _open(client, token).status_code == 404


def test_the_token_never_reaches_a_log_line(client, db_session, caplog):
    """ADR-193: the path an access log keeps holds no token, and the header
    that carries it is a secret field by name."""

    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id)

    with caplog.at_level(logging.DEBUG):
        assert _open(client, issued.token).status_code == 200
        assert _open(client, "B" * 43).status_code == 404
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "/v1/document-links/current" in logged  # the request line was logged
    assert issued.token not in logged and "B" * 43 not in logged
    assert is_secret_field("X-Document-Link") and is_secret_field("x-document-link")


def test_an_expired_link_is_404(client, db_session):
    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id, now=utcnow() - dt.timedelta(days=LINK_VALID_DAYS, seconds=1))

    assert _open(client, issued.token).status_code == 404


def test_a_link_never_serves_another_betriebs_document(client, db_session):
    """Tenant-partitioned: a link issued by Betrieb B naming Betrieb A's
    document finds nothing — the document is read inside B only."""

    owner = _dealership(db_session, "Garage A")
    other = _dealership(db_session, "Garage B")
    document = _offer_document(db_session, owner)
    issued = _issue(db_session, other, document.id)

    assert _open(client, issued.token).status_code == 404


def test_a_link_never_serves_a_document_of_another_type(client, db_session):
    """An offer link names an offer document; a contract link may not serve it."""

    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id, message_type="sales_contract")

    assert _open(client, issued.token).status_code == 404


def test_an_offboarded_betriebs_link_is_404(client, db_session):
    dealership = _dealership(db_session)
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id)
    dealership.status = DealershipStatus.OFFBOARDED
    db_session.commit()

    assert _open(client, issued.token).status_code == 404


def test_opens_are_rate_limited_per_link_in_a_rolling_window(db_session, monkeypatch):
    dealership = _dealership(db_session)
    rendered = RenderedDocument(pdf=b"%PDF-fake", filename="x.pdf")
    monkeypatch.setitem(document_links._SOURCES, "offer", lambda _db, _tenant, _doc: rendered)
    issued = _issue(db_session, dealership, uuid.uuid4())
    start = utcnow()

    for i in range(OPENS_PER_WINDOW):
        assert document_links.open_document_link(db_session, issued.token, now=start + dt.timedelta(seconds=i))
    assert document_links.open_document_link(db_session, issued.token, now=start + dt.timedelta(minutes=30)) is None
    # A refused open is not counted as an open.
    row = db_session.scalar(select(DocumentLink).where(DocumentLink.id == issued.link_id))
    assert row.open_count == OPENS_PER_WINDOW
    # The window rolls over.
    assert document_links.open_document_link(db_session, issued.token, now=start + OPEN_WINDOW) == rendered


# --- registration -----------------------------------------------------------------


def test_sales_registers_a_source_for_its_documents_only():
    assert document_links.has_document_source("offer")
    assert document_links.has_document_source("sales_contract")
    # No order-confirmation document exists yet, so no link may name one.
    assert not document_links.has_document_source("order_confirmation")


def test_a_link_to_a_type_without_a_source_is_refused(db_session):
    dealership = _dealership(db_session)
    with pytest.raises(ValueError, match="order_confirmation"):
        issue_document_link(
            db_session,
            tenant_id=dealership.id,
            message_type="order_confirmation",
            document_id=uuid.uuid4(),
            actor_id=None,
        )


def test_registering_a_source_is_checked(monkeypatch):
    monkeypatch.setattr(document_links, "_SOURCES", dict(document_links._SOURCES))
    with pytest.raises(MessageTypeRegistrationError, match="unregistered"):
        register_document_source("no_such_type", lambda *_: None)
    with pytest.raises(MessageTypeRegistrationError, match="already has"):
        register_document_source("offer", lambda *_: None)
    # The same source again is harmless (the composition root may run twice).
    register_document_source("offer", document_links._SOURCES["offer"])
