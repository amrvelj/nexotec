"""KAN-217 PR-6 (D-A-41, FR-A-51–54; ADR-207): what the send dialog reads before
a send — `GET /v1/sales/documents/{id}/send-options` — and the delivery chip's
`lastDelivery` on the document list.

The read answers per channel what the send would answer now: Customers'
verdict, the channel's readiness from integration, the text from platform's
library. It writes nothing: no secure link, no delivery row, no provider call.
Fake mail and messaging providers only."""

import uuid

from sqlalchemy import func, select

from app.customer.models.customer import Customer, CustomerPhone, PreferredChannel
from app.integration.models.mail import MessageDelivery
from app.integration.public import latest_deliveries
from app.platform.documents.models.document_link import DocumentLink
from tests.cross_context.test_kan217_send_route import (
    _approve_whatsapp,
    _as,
    _dealer,
    _offer_document,
    _send,
    provider,
    scene,
)
from tests.identapi_support import make_user
from tests.integration.test_kan215_mail import _connect_own_ms, app_registrations, fakes
from tests.integration.test_kan217_messaging_providers import _connection as _messaging_connection

__all__ = ["app_registrations", "fakes", "provider", "scene"]  # pytest fixtures, used by name


def _options(client, author, document_id):
    response = client.get(f"/v1/sales/documents/{document_id}/send-options", headers=_as(author))
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {option["channel"]: option for option in body["channels"]}


def _written(db_session) -> tuple[int, int]:
    db_session.expire_all()
    return (
        db_session.scalar(select(func.count()).select_from(DocumentLink)),
        db_session.scalar(select(func.count()).select_from(MessageDelivery)),
    )


def test_nothing_connected_names_each_channels_reason_and_preselects_nothing(client, db_session, scene):
    _dealer_id, author, _customer_id, document = scene

    body, channels = _options(client, author, document.id)

    assert body["documentId"] == str(document.id) and body["customerLabel"] == "Anna Muster"
    assert list(channels) == ["email", "sms", "whatsapp"]
    assert channels["email"]["ready"] is False and channels["email"]["blocked"] == "no_mailbox_connected"
    assert channels["sms"]["blocked"] == "messaging_not_connected"
    assert channels["whatsapp"]["blocked"] == "messaging_not_connected"
    # The dialog never opens on a channel it cannot send on.
    assert body["preselectedChannel"] is None
    assert _written(db_session) == (0, 0)


def test_an_own_mailbox_makes_email_ready_with_the_text_the_send_would_carry(client, db_session, fakes, app_registrations, scene):
    providers, _ = fakes
    _dealer_id, author, _customer_id, document = scene
    _connect_own_ms(client, providers, author)

    body, channels = _options(client, author, document.id)

    email = channels["email"]
    assert email["ready"] is True and email["blocked"] is None
    assert email["senderAddress"] == "anna@garage-muster.ch"
    assert email["address"] == "anna.muster@example.ch" and email["allowed"] is True
    assert email["delivery"] == "attachment" and email["textEditable"] is True
    assert email["language"] == "de"
    assert email["text"].startswith("Sehr geehrte Frau Muster") and "{{" not in email["text"]
    assert "O-2026/000217" in email["text"]
    assert body["preselectedChannel"] == "email"
    # A read: nothing is sent and nothing is written.
    assert providers.sent == [] and _written(db_session) == (0, 0)

    # The preview is the send: sending without a text of one's own sends exactly it.
    sent = _send(client, author, document.id, channel="email")
    assert sent.status_code == 201, sent.text
    assert providers.sent[0].body["message"]["body"]["content"] == email["text"]


def test_customers_refusal_is_the_channels_first_answer(client, db_session, provider, scene):
    dealer, author, customer_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    for phone in db_session.scalars(select(CustomerPhone).where(CustomerPhone.customer_id == customer_id)):
        phone.do_not_use = True
        phone.do_not_use_reason = "falsche Nummer"
    db_session.commit()

    _body, channels = _options(client, author, document.id)

    sms = channels["sms"]
    assert sms["ready"] is True and sms["allowed"] is False
    assert sms["blocked"] == "recipient_refused" and sms["refusal"] == "no_usable_contact"
    assert sms["address"] is None
    # Refused before «not connected» too, as the send answers (WhatsApp is not connected).
    assert channels["whatsapp"]["blocked"] == "recipient_refused"


def test_an_sms_carries_a_secure_link_and_its_text_is_editable(client, db_session, provider, scene):
    dealer, author, _customer_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)

    _body, channels = _options(client, author, document.id)

    sms = channels["sms"]
    assert sms["blocked"] is None and sms["address"] == "+41790000217"
    assert sms["delivery"] == "secure_link" and sms["textEditable"] is True
    assert sms["text"].startswith("Sehr geehrte Frau Muster\nIhre Offerte O-2026/000217")
    # The link is issued by the send only, never by the preview.
    assert "/d?" not in sms["text"] and _written(db_session) == (0, 0)


def test_whatsapp_without_an_approved_template_is_blocked_and_never_editable(client, db_session, provider, scene):
    dealer, author, _customer_id, document = scene
    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)

    _body, channels = _options(client, author, document.id)
    assert channels["whatsapp"]["blocked"] == "whatsapp_template_not_approved"
    assert channels["whatsapp"]["textEditable"] is False

    _approve_whatsapp(db_session, dealer)
    _body, channels = _options(client, author, document.id)
    assert channels["whatsapp"]["blocked"] is None
    assert channels["whatsapp"]["text"].startswith("Sehr geehrte Frau Muster, Ihre Offerte O-2026/000217")


def test_the_customers_preferred_channel_is_preselected_only_when_it_is_ready(client, db_session, provider, scene):
    dealer, author, customer_id, document = scene
    customer = db_session.get(Customer, customer_id)
    customer.preferred_channel = PreferredChannel.WHATSAPP
    db_session.commit()

    body, _channels = _options(client, author, document.id)
    assert body["preselectedChannel"] is None  # WhatsApp is not connected

    _messaging_connection(db_session, "meta_whatsapp", tenant_id=dealer)
    body, _channels = _options(client, author, document.id)
    assert body["preselectedChannel"] == "whatsapp"


def test_another_betriebs_document_is_404(client, db_session, scene):
    _dealer_id, _author, _customer_id, document = scene
    other = _dealer(client)
    stranger = make_user(db_session, tenant_id=other, email="fremd@garage-anders.ch")

    response = client.get(f"/v1/sales/documents/{document.id}/send-options", headers=_as(stranger))

    assert response.status_code == 404


def test_a_document_without_a_customer_is_refused_by_reason(client, db_session, scene):
    dealer, author, _customer_id, _document = scene
    document = _offer_document(db_session, tenant_id=dealer, customer_id=None, number="O-2026/000218")

    response = client.get(f"/v1/sales/documents/{document.id}/send-options", headers=_as(author))

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "document_has_no_customer"


# -- the delivery chip: lastDelivery on the version list --------------------------


def test_the_version_list_carries_each_documents_newest_send(client, db_session, provider, scene):
    dealer, author, _customer_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    offer_id = document.owner_id

    listed = client.get(f"/v1/sales/offers/{offer_id}/documents", headers=_as(author)).json()["items"]
    assert [item["lastDelivery"] for item in listed] == [None]

    first = _send(client, author, document.id, channel="sms")
    assert first.status_code == 201, first.text
    provider["twilio"].status, provider["twilio"].body = 400, {"code": 21211, "message": "invalid number"}
    second = _send(client, author, document.id, channel="sms")
    assert second.status_code == 201, second.text

    (item,) = client.get(f"/v1/sales/offers/{offer_id}/documents", headers=_as(author)).json()["items"]
    delivery = item["lastDelivery"]
    assert delivery["deliveryId"] == second.json()["deliveryId"]
    assert delivery["channel"] == "sms" and delivery["status"] == "failed"
    assert delivery["failureCode"] == "provider_refused_message" and delivery["sentAt"]


def test_another_betriebs_delivery_never_reaches_the_chip(client, db_session, provider, scene):
    """`latest_deliveries` reads the acting Betrieb's rows only, even for a
    document id that another Betrieb's row names."""

    dealer, author, _customer_id, document = scene
    _messaging_connection(db_session, "twilio", tenant_id=dealer)
    assert _send(client, author, document.id, channel="sms").status_code == 201

    assert set(latest_deliveries(db_session, tenant_id=dealer, document_ids=[document.id])) == {document.id}
    assert latest_deliveries(db_session, tenant_id=uuid.uuid4(), document_ids=[document.id]) == {}
