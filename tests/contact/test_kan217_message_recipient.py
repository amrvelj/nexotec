"""KAN-217 PR-2 — Contacts' read for the send dialog (FR-A-52, FR-A-53; D-17,
D-21, ADR-067): where a message on e-mail, SMS or WhatsApp goes, the consent
verdict for a transactional or a marketing send, and the channel the dialog
opens on."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import update

from app.core.auth import create_access_token
from app.contact.models.contact import Contact, ContactEmail, ContactLifecycleStatus, ContactPhone
from app.contact.public import message_recipient, preselected_message_channel
from app.platform.public import MessageChannel, MessagePurpose
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

_EMAIL, _SMS, _WHATSAPP = MessageChannel.EMAIL, MessageChannel.SMS, MessageChannel.WHATSAPP
_TX, _MKT = MessagePurpose.TRANSACTIONAL, MessagePurpose.MARKETING


def _bearer(*, tenant_id: uuid.UUID, platform_admin: bool = False) -> dict[str, str]:
    from app.core.auth import AccessRole

    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}) if platform_admin else frozenset(),
        is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _dealer(client) -> str:
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": {
            "street": "Bahnhofstrasse",
            "houseNumber": "1",
            "postalCode": "8001",
            "locality": "Zürich",
            "canton": "ZH",
        },
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(tenant_id=uuid.uuid4(), platform_admin=True))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _contact(client, dealer_id: str, **overrides) -> uuid.UUID:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "fr",
        "emails": [{"emailType": "personal", "emailAddress": "anna@example.ch"}],
        "phones": [{"phoneType": "mobile", "phoneE164": "+41791234567"}],
        **overrides,
    }
    response = client.post("/v1/contacts", json=payload, headers=_bearer(tenant_id=uuid.UUID(dealer_id)))
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


@pytest.fixture()
def scene(client, db_session):
    dealer = _dealer(client)
    contact_id = _contact(client, dealer)
    group_id = db_session.get(Contact, contact_id).group_id
    return dealer, contact_id, group_id


def _read(db_session, group_id, contact_id, channel, purpose=_TX):
    db_session.expire_all()
    return message_recipient(db_session, group_id=group_id, contact_id=contact_id, channel=channel, purpose=purpose)


def test_a_transactional_send_needs_only_a_usable_row_on_the_channel(db_session, scene):
    _dealer_id, contact_id, group_id = scene
    email = _read(db_session, group_id, contact_id, _EMAIL)
    assert email.allowed and email.refusal is None
    assert email.address == "anna@example.ch" and email.language == "fr"
    for channel in (_SMS, _WHATSAPP):
        mobile = _read(db_session, group_id, contact_id, channel)
        assert mobile.allowed and mobile.address == "+41791234567"


def test_a_row_flagged_nicht_verwenden_or_closed_is_no_recipient(db_session, scene):
    _dealer_id, contact_id, group_id = scene
    db_session.execute(update(ContactPhone).where(ContactPhone.contact_id == contact_id).values(do_not_use=True))
    db_session.commit()
    sms = _read(db_session, group_id, contact_id, _SMS)
    assert not sms.allowed and sms.refusal == "no_usable_contact" and sms.address is None
    # The e-mail row is still usable — until it is closed.
    assert _read(db_session, group_id, contact_id, _EMAIL).allowed
    db_session.execute(
        update(ContactEmail).where(ContactEmail.contact_id == contact_id).values(valid_to=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    )
    db_session.commit()
    closed = _read(db_session, group_id, contact_id, _EMAIL)
    assert not closed.allowed and closed.refusal == "no_usable_contact" and closed.address is None


def test_a_contact_with_only_a_landline_gets_no_sms_or_whatsapp(client, db_session, scene):
    dealer_id, _contact_id, group_id = scene
    landline_only = _contact(client, dealer_id, phones=[{"phoneType": "landline", "phoneE164": "+41441112233"}])
    for channel in (_SMS, _WHATSAPP):
        assert _read(db_session, group_id, landline_only, channel).refusal == "no_usable_contact"


def test_the_work_address_is_used_when_there_is_no_personal_one(client, db_session, scene):
    dealer_id, _contact_id, group_id = scene
    business = _contact(client, dealer_id, emails=[{"emailType": "work", "emailAddress": "info@firma.ch"}])
    assert _read(db_session, group_id, business, _EMAIL).address == "info@firma.ch"


def test_nicht_kontaktieren_refuses_marketing_and_lets_a_transactional_send_through(db_session, scene):
    """Anto, 2026-11-19 (KAN-217, option 2): a contact who asked for an
    offer, an order confirmation or a contract gets it; marketing stays
    refused, even with every D-17 gate open."""

    _dealer_id, contact_id, group_id = scene
    db_session.execute(
        update(Contact)
        .where(Contact.id == contact_id)
        .values(lifecycle_status=ContactLifecycleStatus.DO_NOT_CONTACT, marketing_consent=True, newsletter=True)
    )
    db_session.execute(
        update(ContactEmail)
        .where(ContactEmail.contact_id == contact_id)
        .values(consent_granted=True, consent_scope="marketing")
    )
    db_session.commit()
    for channel in (_EMAIL, _SMS, _WHATSAPP):
        transactional = _read(db_session, group_id, contact_id, channel, _TX)
        assert transactional.allowed and transactional.refusal is None and transactional.address
        assert _read(db_session, group_id, contact_id, channel, _MKT).refusal == "contact_do_not_contact"


def test_a_merged_contact_is_refused(client, db_session, scene):
    dealer_id, contact_id, group_id = scene
    survivor = _contact(client, dealer_id, lastName="Überlebend")
    db_session.execute(
        update(Contact)
        .where(Contact.id == contact_id)
        .values(lifecycle_status=ContactLifecycleStatus.MERGED, duplicate_of_contact_id=survivor)
    )
    db_session.commit()
    assert _read(db_session, group_id, contact_id, _EMAIL).refusal == "contact_merged"


def test_a_marketing_send_needs_the_three_gates_of_d17_in_order(db_session, scene):
    _dealer_id, contact_id, group_id = scene
    assert _read(db_session, group_id, contact_id, _EMAIL, _MKT).refusal == "marketing_consent_missing"

    db_session.execute(update(Contact).where(Contact.id == contact_id).values(marketing_consent=True))
    db_session.commit()
    assert _read(db_session, group_id, contact_id, _EMAIL, _MKT).refusal == "channel_consent_missing"

    # A grant scoped to invoicing never authorises a campaign (FR-23 §1).
    db_session.execute(
        update(ContactEmail)
        .where(ContactEmail.contact_id == contact_id)
        .values(consent_granted=True, consent_scope="invoicing")
    )
    db_session.commit()
    assert _read(db_session, group_id, contact_id, _EMAIL, _MKT).refusal == "channel_consent_missing"

    db_session.execute(
        update(ContactEmail).where(ContactEmail.contact_id == contact_id).values(consent_scope="marketing")
    )
    db_session.commit()
    assert _read(db_session, group_id, contact_id, _EMAIL, _MKT).refusal == "newsletter_not_subscribed"

    db_session.execute(update(Contact).where(Contact.id == contact_id).values(newsletter=True))
    db_session.commit()
    assert _read(db_session, group_id, contact_id, _EMAIL, _MKT).allowed
    # The mobile row has no grant of its own: SMS marketing stays refused.
    assert _read(db_session, group_id, contact_id, _SMS, _MKT).refusal == "channel_consent_missing"


def test_a_contact_of_another_group_is_none(db_session, scene):
    _dealer_id, contact_id, _group_id = scene
    assert _read(db_session, uuid.uuid4(), contact_id, _EMAIL) is None
    assert preselected_message_channel(db_session, group_id=uuid.uuid4(), contact_id=contact_id) == (False, None)


@pytest.mark.parametrize(
    ("preferred", "with_email", "expected"),
    [
        ("email", True, _EMAIL),
        ("whatsapp", True, _WHATSAPP),
        ("phone", True, _EMAIL),
        ("post", True, _EMAIL),
        (None, True, _EMAIL),
        ("phone", False, None),
        ("post", False, None),
    ],
)
def test_the_dialog_opens_on_the_preferred_channel(client, db_session, preferred, with_email, expected):
    dealer = _dealer(client)
    overrides: dict = {"preferredChannel": preferred}
    if not with_email:
        overrides["emails"] = []
    contact_id = _contact(client, dealer, **overrides)
    group_id = db_session.get(Contact, contact_id).group_id
    assert preselected_message_channel(db_session, group_id=group_id, contact_id=contact_id) == (True, expected)
