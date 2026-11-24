"""KAN-217 PR-5 (D-A-41, FR-A-51–54, A-RULE-10; ADR-204): Sales sends an offer
or contract document to its contact — `POST /v1/sales/documents/{id}/send`.

Contacts decides whether the send may go (`message_recipient`), platform's
library gives the text and the secure link, integration's `deliver_message`
sends it and writes the delivery record. The flow spans four contexts, hence
cross_context. Fake mail and messaging providers only: nothing real is
called."""

import json
import uuid
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole
from app.contact.models.contact import Contact, ContactLifecycleStatus, ContactPhone
from app.integration.adapters import messaging_providers
from app.integration.models.mail import DeliveryChannel, DeliveryStatus, MessageDelivery
from app.integration.services import resilience, secrets_backend
from app.platform.documents.models.document_link import DocumentLink
from app.platform.documents.models.standard_text import StandardText, StandardTextScope, StandardTextSource
from app.platform.documents.services.standard_text import install_platform_seeds
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.document import generate_contract_document, generate_offer_document
from tests.dealership_support import valid_uid
from tests.identapi_support import make_user
from tests.integration.test_kan215_mail import _connect_business_ms, _connect_own_ms, app_registrations, fakes
from tests.integration.test_kan217_messaging_providers import _META_ACCEPTED, _TWILIO_ACCEPTED, Recorder
from tests.integration.test_kan217_messaging_providers import _connection as _messaging_connection
from tests.membership_support import bearer, token

__all__ = ["app_registrations", "fakes"]  # pytest fixtures, used by name

_SECRET = "invented-secret-value-kan217-send"


def _platform_admin() -> dict[str, str]:
    return bearer(token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.uuid4()))


def _dealer(client) -> uuid.UUID:
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": {"street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_platform_admin())
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _as(user) -> dict[str, str]:
    return bearer(token(tenant_id=user.tenant_id, user_id=user.id, is_dealer_manager=True))


def _contact(client, author, **overrides) -> uuid.UUID:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "salutation": "frau",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": "anna.muster@example.ch"}],
        "phones": [{"phoneType": "mobile", "phoneE164": "+41790000217"}],
        **overrides,
    }
    response = client.post("/v1/contacts", json=payload, headers=_as(author))
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _offer_document(db, *, tenant_id, contact_id, number="O-2026/000217"):
    offer = SalesOffer(
        tenant_id=tenant_id, offer_number=number, status=OfferStatus.OPEN, contact_id=contact_id,
        contact_label="Anna Muster", vehicle_label="VW Golf GTI", owner_label="Ben Verkäufer",
        base_price=Decimal("38000.00"), list_price=Decimal("38000.00"), gross_price=Decimal("36000.00"),
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return generate_offer_document(db, offer=offer, actor_id=None)


@pytest.fixture
def scene(client, db_session):
    install_platform_seeds(db_session)
    db_session.commit()
    dealer = _dealer(client)
    author = make_user(db_session, tenant_id=dealer, email="anna@garage-muster.ch", first_name="Lea", last_name="Senderin")
    contact_id = _contact(client, author)
    document = _offer_document(db_session, tenant_id=dealer, contact_id=contact_id)
    return dealer, author, contact_id, document


@pytest.fixture
def provider(monkeypatch):
    """A fake Twilio / Meta that records each request."""

    recorders: dict[str, Recorder] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        recorder = recorders["meta" if "graph.facebook.com" in str(request.url) else "twilio"]
        return recorder(request)

    monkeypatch.setattr(messaging_providers, "http_transport", lambda: httpx.MockTransport(transport))
    monkeypatch.setattr(secrets_backend, "resolve_secret", lambda *, connection_id, slot: _SECRET)
    recorders["twilio"] = Recorder(body=_TWILIO_ACCEPTED, status=201)
    recorders["meta"] = Recorder(body=_META_ACCEPTED)
    yield recorders
    resilience._CIRCUITS.clear()


def _send(client, author, document_id, **body):
    return client.post(f"/v1/sales/documents/{document_id}/send", json=body, headers=_as(author))


def _deliveries(db_session):
    db_session.expire_all()
    return db_session.scalars(select(MessageDelivery).order_by(MessageDelivery.created_at)).all()


def _approve_whatsapp(db_session, dealer, *, template="offer_sent_v1"):
    """The Betrieb's own approved copy of the offer's WhatsApp text (FR-A-54)."""

    seed = db_session.scalar(
        select(StandardText).where(
            StandardText.code == "whatsapp.offer_sent", StandardText.dealership_id.is_(None), StandardText.language == "de"
        )
    )
    own = StandardText(
        dealership_id=dealer, code=seed.code, category=seed.category, applies_to=seed.applies_to, slot=seed.slot,
        language=seed.language, title=seed.title, body="{{customer.salutation}}, Ihre Offerte {{document.number}}: ",
        scope=StandardTextScope.DEALERSHIP, source=StandardTextSource.DEALER, overridden_from_id=seed.id,
        overridden_seed_version=seed.version, is_default=True, active=True, sort_order=seed.sort_order,
        template_approved=True, provider_template_id=template,
    )
    db_session.add(own)
    db_session.commit()


# -- e-mail -------------------------------------------------------------------------


def test_an_email_leaves_from_the_authors_own_mailbox_with_the_pdf_and_one_delivery_row(client, db_session, fakes, app_registrations, scene):
    providers, _ = fakes
    _dealer_id, author, contact_id, document = scene
    _connect_own_ms(client, providers, author)

    response = _send(client, author, document.id, channel="email")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["channel"] == "email" and body["status"] == "sent"
    assert body["senderAddress"] == "anna@garage-muster.ch"
    (sent,) = providers.sent
    message = sent.body["message"]
    assert message["toRecipients"][0]["emailAddress"]["address"] == "anna.muster@example.ch"
    assert message["subject"] == "Offerte O-2026/000217"
    assert message["attachments"][0]["name"] == "O-2026-000217.pdf"
    text = message["body"]["content"]
    assert "Sehr geehrte Frau Muster" in text and "{{" not in text
    (delivery,) = _deliveries(db_session)
    assert delivery.channel == DeliveryChannel.EMAIL and delivery.document_id == document.id
    assert delivery.contact_id == contact_id and delivery.contact_label == "Anna Muster"
    assert delivery.body_text == text and delivery.document_type == "offer"


def test_without_an_own_mailbox_the_shared_one_sends_and_without_either_nothing_is_written(client, db_session, fakes, app_registrations, scene):
    providers, _ = fakes
    dealer, author, _contact_id, document = scene

    refused = _send(client, author, document.id, channel="email")
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "no_mailbox_connected"
    assert _deliveries(db_session) == []

    manager = make_user(db_session, tenant_id=dealer, email="chef@garage-muster.ch")
    _connect_business_ms(client, providers, manager)
    response = _send(client, author, document.id, channel="email")
    assert response.status_code == 201, response.text
    assert response.json()["senderAddress"] == "info@garage-muster.ch"


def test_the_authors_own_wording_replaces_the_standard_text_on_email(client, db_session, fakes, app_registrations, scene):
    providers, _ = fakes
    _dealer_id, author, _contact_id, document = scene
    _connect_own_ms(client, providers, author)

    response = _send(client, author, document.id, channel="email", text="Guten Tag Frau Muster, anbei die Offerte.")

    assert response.status_code == 201, response.text
    assert providers.sent[0].body["message"]["body"]["content"] == "Guten Tag Frau Muster, anbei die Offerte."


# -- SMS ----------------------------------------------------------------------------


def test_an_sms_carries_the_text_and_the_secure_link_and_names_no_number_in_the_call_log(client, db_session, provider, scene):
    dealer, author, contact_id, document = scene
    connection = _messaging_connection(db_session, "twilio", tenant_id=dealer)

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 201, response.text
    assert response.json() == {
        "deliveryId": response.json()["deliveryId"], "channel": "sms", "status": "sent", "senderAddress": None,
        "failureCode": None, "providerStatus": "accepted",
    }
    (request,) = provider["twilio"].requests
    form = parse_qs(request.content.decode())
    assert form["To"] == ["+41790000217"]
    sms = form["Body"][0]
    assert sms.startswith("Sehr geehrte Frau Muster\nIhre Offerte O-2026/000217 für VW Golf GTI ist bereit.")
    link_line = sms.rsplit("\n", 1)[1]
    assert "/d?lang=de#" in link_line
    (link,) = db_session.scalars(select(DocumentLink)).all()
    assert link.document_id == document.id and link.tenant_id == dealer
    (delivery,) = _deliveries(db_session)
    assert (delivery.channel, delivery.connection_id, delivery.recipients) == (
        DeliveryChannel.SMS, connection.id, ["+41790000217"]
    )
    # The record keeps the text, never the link: only the token's hash is stored (ADR-193).
    assert delivery.document_link_id == link.id and delivery.body_text == sms.rsplit("\n", 1)[0]
    assert link_line.split("#", 1)[1] not in (delivery.body_text or "")
    assert delivery.sending_identity_id is None and delivery.subject is None
    assert delivery.provider_message_id == _TWILIO_ACCEPTED["sid"]
    assert delivery.contact_id == contact_id


def test_an_sms_without_a_connected_provider_is_refused_and_leaves_no_link(client, db_session, provider, scene):
    _dealer_id, author, _contact_id, document = scene

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "messaging_not_connected"
    assert db_session.scalar(select(func.count()).select_from(DocumentLink)) == 0
    assert _deliveries(db_session) == []


def test_a_provider_failure_is_a_failed_delivery_row_not_a_500(client, db_session, provider, scene):
    dealer, author, _contact_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    provider["twilio"].status, provider["twilio"].body = 400, {"code": 21211, "message": "invalid number"}

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed" and response.json()["failureCode"] == "provider_refused_message"
    (delivery,) = _deliveries(db_session)
    assert delivery.status == DeliveryStatus.FAILED and delivery.provider_message_id is None


def test_an_sms_text_too_long_with_its_link_is_refused_before_the_provider(client, db_session, provider, scene):
    dealer, author, _contact_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)

    response = _send(client, author, document.id, channel="sms", text="x" * 1590)

    assert response.status_code == 422 and response.json()["error"]["details"]["reason"] == "message_invalid"
    assert provider["twilio"].requests == [] and _deliveries(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(DocumentLink)) == 0


# -- WhatsApp: approved templates only (FR-A-54) -----------------------------------


def test_whatsapp_refuses_a_text_the_provider_has_not_approved(client, db_session, provider, scene):
    dealer, author, _contact_id, document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)

    response = _send(client, author, document.id, channel="whatsapp")

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"reason": "whatsapp_template_not_approved", "link": "/administration/texts"}
    assert provider["meta"].requests == []
    assert db_session.scalar(select(func.count()).select_from(DocumentLink)) == 0


def test_whatsapp_sends_the_approved_template_with_its_values_and_the_link_last(client, db_session, provider, scene):
    dealer, author, _contact_id, document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)
    _approve_whatsapp(db_session, dealer)

    response = _send(client, author, document.id, channel="whatsapp")

    assert response.status_code == 201, response.text
    (request,) = provider["meta"].requests
    payload = json.loads(request.content)
    assert payload["type"] == "template" and payload["to"] == "+41790000217"
    assert payload["template"]["name"] == "offer_sent_v1" and payload["template"]["language"]["code"] == "de"
    (component,) = payload["template"]["components"]
    values = [parameter["text"] for parameter in component["parameters"]]
    assert values[:2] == ["Sehr geehrte Frau Muster", "O-2026/000217"]
    assert "/d?lang=de#" in values[2] and len(values) == 3
    (delivery,) = _deliveries(db_session)
    assert delivery.channel == DeliveryChannel.WHATSAPP and delivery.provider_message_id == "wamid.invented-kan217"


def test_whatsapp_refuses_the_authors_own_wording(client, db_session, provider, scene):
    dealer, author, _contact_id, document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)
    _approve_whatsapp(db_session, dealer)

    response = _send(client, author, document.id, channel="whatsapp", text="Hallo!")

    assert response.status_code == 422 and response.json()["error"]["details"]["reason"] == "whatsapp_text_fixed"
    assert provider["meta"].requests == []


# -- consent, scope, permissions ----------------------------------------------------


def test_contacts_refusal_is_the_answer_and_nothing_is_sent_or_linked(client, db_session, provider, scene):
    dealer, author, contact_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    # The contact's only mobile number marked «nicht verwenden»: no usable row on SMS.
    for phone in db_session.scalars(select(ContactPhone).where(ContactPhone.contact_id == contact_id)):
        phone.do_not_use = True
        phone.do_not_use_reason = "falsche Nummer"
    db_session.commit()

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "recipient_refused", "refusal": "no_usable_contact", "channel": "sms"}
    assert provider["twilio"].requests == [] and _deliveries(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(DocumentLink)) == 0


def test_a_do_not_contact_contact_still_receives_a_transactional_send(client, db_session, provider, scene):
    """Anto, 2026-11-19 (option 2): «Nicht kontaktieren» refuses marketing only."""

    dealer, author, contact_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    db_session.get(Contact, contact_id).lifecycle_status = ContactLifecycleStatus.DO_NOT_CONTACT
    db_session.commit()

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 201, response.text


def test_another_betriebs_document_is_404(client, db_session, provider, scene):
    _dealer_id, _author, _contact_id, document = scene
    other = _dealer(client)
    stranger = make_user(db_session, tenant_id=other, email="fremd@example.ch")

    response = _send(client, stranger, document.id, channel="sms")

    assert response.status_code == 404


def test_a_contract_document_is_sent_as_a_sales_contract(client, db_session, provider, scene):
    dealer, author, contact_id, _document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    contract = SalesContract(
        tenant_id=dealer, contract_number="C-000217", status=ContractStatus.PENDING, contact_id=contact_id,
        contact_label="Anna Muster", vehicle_source="manual", vehicle_label="VW Golf GTI",
        base_price=Decimal("38000.00"), list_price=Decimal("38000.00"), gross_price=Decimal("36000.00"),
    )
    db_session.add(contract)
    db_session.commit()
    document = generate_contract_document(db_session, contract=contract, actor_id=None)

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 201, response.text
    (delivery,) = _deliveries(db_session)
    assert delivery.document_type == "sales_contract"
    assert "C-000217" in delivery.body_text


def test_an_unknown_channel_is_422(client, scene):
    _dealer_id, author, _contact_id, document = scene
    assert _send(client, author, document.id, channel="fax").status_code == 422


def test_a_refused_contact_is_told_the_refusal_before_anything_else(client, db_session, provider, scene):
    """Review finding 4: Contacts' verdict is the first answer, even to a
    request that would also be refused for its WhatsApp wording."""

    dealer, author, contact_id, document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)
    for phone in db_session.scalars(select(ContactPhone).where(ContactPhone.contact_id == contact_id)):
        phone.do_not_use = True
        phone.do_not_use_reason = "falsche Nummer"
    db_session.commit()

    response = _send(client, author, document.id, channel="whatsapp", text="Hallo!")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["refusal"] == "no_usable_contact"


def test_a_text_in_a_fallback_language_carries_the_whole_message_in_that_language(client, db_session, provider, scene):
    """Review finding 3: the Betrieb switched its Italian WhatsApp text off, so
    the library falls back to German — the opening, the link's page and the
    template's language follow the German text, never a mix."""

    dealer, author, _contact_id, _document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)
    _approve_whatsapp(db_session, dealer)
    italian = _contact(
        client, author, firstName="Giulia", lastName="Rossi", language="it",
        emails=[{"emailType": "personal", "emailAddress": "giulia@example.ch"}],
        phones=[{"phoneType": "mobile", "phoneE164": "+41790000218"}],
    )
    seed = db_session.scalar(
        select(StandardText).where(
            StandardText.code == "whatsapp.offer_sent", StandardText.dealership_id.is_(None), StandardText.language == "it"
        )
    )
    db_session.add(
        StandardText(
            dealership_id=dealer, code=seed.code, category=seed.category, applies_to=seed.applies_to, slot=seed.slot,
            language=seed.language, title=seed.title, body=seed.body, scope=StandardTextScope.DEALERSHIP,
            source=StandardTextSource.DEALER, overridden_from_id=seed.id, overridden_seed_version=seed.version,
            is_default=True, active=False, sort_order=seed.sort_order,
        )
    )
    db_session.commit()
    document = _offer_document(db_session, tenant_id=dealer, contact_id=italian, number="O-2026/000218")

    response = _send(client, author, document.id, channel="whatsapp")

    assert response.status_code == 201, response.text
    payload = json.loads(provider["meta"].requests[0].content)
    assert payload["template"]["language"]["code"] == "de"
    values = [parameter["text"] for parameter in payload["template"]["components"][0]["parameters"]]
    assert values[0] == "Sehr geehrte Frau Rossi" and "/d?lang=de#" in values[-1]


def test_a_name_longer_than_the_label_column_still_records_the_send(client, db_session, provider, scene):
    """Review finding 5: a first and a last name of 100 characters each make a
    201-character name; the record keeps the first 200 instead of failing
    after the provider already sent."""

    dealer, author, _contact_id, _document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    long_named = _contact(
        client, author, firstName="A" * 100, lastName="B" * 100,
        emails=[{"emailType": "personal", "emailAddress": "lang@example.ch"}],
        phones=[{"phoneType": "mobile", "phoneE164": "+41790000219"}],
    )
    document = _offer_document(db_session, tenant_id=dealer, contact_id=long_named, number="O-2026/000219")

    response = _send(client, author, document.id, channel="sms")

    assert response.status_code == 201, response.text
    (delivery,) = _deliveries(db_session)
    assert delivery.contact_label == ("A" * 100 + " " + "B" * 100)[:200]


def test_the_authors_own_wording_keeps_the_contacts_language_whatever_the_library_holds(client, db_session, provider, scene):
    """Review 2, finding 4: an author who writes the Italian contact in
    Italian gets the Italian link page, even when the Betrieb's SMS text exists
    only in German."""

    dealer, author, _contact_id, _document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    italian = _contact(
        client, author, firstName="Giulia", lastName="Rossi", language="it",
        emails=[{"emailType": "personal", "emailAddress": "giulia.r@example.ch"}],
        phones=[{"phoneType": "mobile", "phoneE164": "+41790000220"}],
    )
    seed = db_session.scalar(
        select(StandardText).where(
            StandardText.code == "sms.offer_sent", StandardText.dealership_id.is_(None), StandardText.language == "it"
        )
    )
    db_session.add(
        StandardText(
            dealership_id=dealer, code=seed.code, category=seed.category, applies_to=seed.applies_to, slot=seed.slot,
            language=seed.language, title=seed.title, body=seed.body, scope=StandardTextScope.DEALERSHIP,
            source=StandardTextSource.DEALER, overridden_from_id=seed.id, overridden_seed_version=seed.version,
            is_default=True, active=False, sort_order=seed.sort_order,
        )
    )
    db_session.commit()
    document = _offer_document(db_session, tenant_id=dealer, contact_id=italian, number="O-2026/000220")

    response = _send(client, author, document.id, channel="sms", text="Gentile Signora Rossi, ecco la Sua offerta.")

    assert response.status_code == 201, response.text
    body = parse_qs(provider["twilio"].requests[0].content.decode())["Body"][0]
    assert body.startswith("Gentile Signora Rossi, ecco la Sua offerta.\n") and "/d?lang=it#" in body
