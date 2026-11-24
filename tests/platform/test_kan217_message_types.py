"""KAN-217 PR-1 — the registry of contact message types (Dealer Administration
FR-A-52, FR-A-53): Sales registers its offer, order confirmation and sales
contract; every registered text ships as a platform seed in four languages and
renders; the migration's literal rows equal the application's declaration; a
wrong registration is refused at start-up."""

import datetime as dt
import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import select

import app.message_type_registry  # noqa: F401 — the composition root fills the registry
from app.core.i18n import SwissLanguage
from app.platform.documents.models.standard_text import StandardText, StandardTextAppliesTo, StandardTextSlot
from app.platform.documents.services import message_types as registry
from app.platform.documents.services.message_text_seed import MESSAGE_SEED_TEXTS
from app.platform.documents.services.standard_text import SHIPPED_SEED_TEXTS
from app.platform.documents.services.standard_text_placeholders import (
    render_body,
    sample_values,
    target_for,
    tokens_in,
    validate_body,
)
from app.platform.public import (
    MessageChannel,
    MessageDelivery,
    MessagePurpose,
    MessageType,
    MessageTypeKind,
    MessageTypeRegistrationError,
    delivery_of,
    get_message_type,
    message_types,
    register_message_type,
)

_SHIPPED = {seed.code: seed for seed in SHIPPED_SEED_TEXTS}
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "platform" / "a4d9e6c3b7f2_kan217_message_texts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("kan217_message_texts", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration()


@pytest.fixture()
def scratch_registry(monkeypatch):
    """A test's own registrations never leak into the registry the app filled."""

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))


def test_sales_registers_its_three_document_types_as_transactional():
    by_code = {t.code: t for t in message_types()}
    assert {"offer", "order_confirmation", "sales_contract"} <= set(by_code)
    for code, applies_to in (
        ("offer", StandardTextAppliesTo.OFFER),
        ("order_confirmation", StandardTextAppliesTo.ORDER_CONFIRMATION),
        ("sales_contract", StandardTextAppliesTo.CONTRACT),
    ):
        message_type = by_code[code]
        assert message_type.module == "sales"
        assert message_type.kind == MessageTypeKind.DOCUMENT and message_type.carries_document
        # Anto, 2026-11-14, Q1 A: an offer, order confirmation or contract is
        # contract preparation — never marketing.
        assert message_type.purpose == MessagePurpose.TRANSACTIONAL
        assert message_type.applies_to == applies_to
        assert message_type.channels == (MessageChannel.EMAIL, MessageChannel.SMS, MessageChannel.WHATSAPP)
    assert get_message_type("offer").text_codes[MessageChannel.EMAIL] == "email.offer_sent"


@pytest.mark.parametrize("message_type", message_types(), ids=lambda t: t.code)
def test_every_registered_text_ships_in_four_languages_and_renders(message_type):
    for channel, code in message_type.text_codes.items():
        seed = _SHIPPED.get(code)
        assert seed is not None, f"{message_type.code}: {code} is not a shipped seed"
        assert seed.category.value == channel.value
        assert seed.applies_to == message_type.applies_to and seed.slot == StandardTextSlot.BODY
        target = target_for(seed.category, seed.applies_to)
        assert target is not None
        for language in SwissLanguage:
            title, body = seed.titles[language].strip(), seed.bodies[language]
            assert title and body.strip(), f"{code} {language.value} is empty"
            validate_body(body, target)
            values = {
                **sample_values(language, today=dt.date(2026, 11, 19)),
                "dealership.name": "Garage Muster AG",
                "location.phone": "+41 44 123 45 67",
            }
            rendered = render_body(body, target=target, values=values)
            # Every brace was a token: a single `{x}` left by an f-string slip
            # would print as it stands.
            assert "{" not in rendered and "}" not in rendered, f"{code} {language.value}: {rendered!r}"
            assert "ß" not in body, f"{code} {language.value}: Swiss spelling writes «ss»"


@pytest.mark.parametrize("seed", [s for s in MESSAGE_SEED_TEXTS if s.category.value != "email"], ids=lambda s: s.code)
def test_an_sms_or_whatsapp_text_names_no_link_and_says_it_is_in_the_message(seed):
    for language in SwissLanguage:
        body = seed.bodies[language]
        assert "http" not in body and "document.link" not in tokens_in(body)
        assert re.search(r"Link|lien|link", body), f"{seed.code} {language.value} does not mention the link"


def test_the_migrations_literal_rows_are_the_application_declaration():
    declared = [
        (
            seed.code,
            seed.category.value,
            seed.applies_to.value,
            seed.slot.value,
            seed.sort_order,
            language.value,
            seed.titles[language],
            seed.bodies[language],
        )
        for seed in MESSAGE_SEED_TEXTS
        for language in SwissLanguage
    ]
    assert list(_MIGRATION.SEED_ROWS) == declared
    # The migration writes every row as its default; so must the declaration.
    assert all(seed.is_default for seed in MESSAGE_SEED_TEXTS)
    assert _MIGRATION._APPLIES_TO == tuple(member.value for member in StandardTextAppliesTo)


def test_the_installed_library_holds_every_message_text_in_four_languages(db_session, standard_text_seeds):
    rows = db_session.execute(
        select(StandardText.code, StandardText.language).where(StandardText.dealership_id.is_(None))
    ).all()
    installed = {(code, language) for code, language in rows}
    for seed in MESSAGE_SEED_TEXTS:
        for language in SwissLanguage:
            assert (seed.code, language) in installed


def test_a_channel_attaches_or_links_the_document_and_a_reminder_sends_text_only():
    offer = get_message_type("offer")
    assert delivery_of(offer, MessageChannel.EMAIL) == MessageDelivery.ATTACHMENT
    assert delivery_of(offer, MessageChannel.SMS) == MessageDelivery.SECURE_LINK
    assert delivery_of(offer, MessageChannel.WHATSAPP) == MessageDelivery.SECURE_LINK
    reminder = MessageType(
        code="vehicle_ready",
        module="aftersales",
        kind=MessageTypeKind.REMINDER,
        purpose=MessagePurpose.TRANSACTIONAL,
        applies_to=StandardTextAppliesTo.VEHICLE_READY,
        text_codes={MessageChannel.EMAIL: "email.vehicle_ready"},
    )
    assert delivery_of(reminder, MessageChannel.EMAIL) == MessageDelivery.TEXT_ONLY


def _type(**overrides) -> MessageType:
    fields = {
        "code": "test_offer_copy",
        "module": "sales",
        "kind": MessageTypeKind.DOCUMENT,
        "purpose": MessagePurpose.TRANSACTIONAL,
        "applies_to": StandardTextAppliesTo.OFFER,
        "text_codes": {MessageChannel.EMAIL: "email.offer_sent"},
        **overrides,
    }
    return MessageType(**fields)


def test_registering_the_same_definition_twice_is_harmless_and_a_conflicting_one_is_refused(scratch_registry):
    register_message_type(_type())
    register_message_type(_type())
    assert get_message_type("test_offer_copy") == _type()
    with pytest.raises(MessageTypeRegistrationError, match="already registered"):
        register_message_type(_type(purpose=MessagePurpose.MARKETING))
    with pytest.raises(MessageTypeRegistrationError, match="already registered"):
        register_message_type(_type(code="offer", module="aftersales"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"code": "Offer Copy"}, "lower-case identifier"),
        ({"module": ""}, "no owning module"),
        ({"text_codes": {}}, "no channel"),
        # An SMS channel pointing at an e-mail body.
        ({"text_codes": {MessageChannel.SMS: "email.offer_sent"}}, "is not a sms body"),
        # No e-mail body exists for a contact note.
        ({"applies_to": StandardTextAppliesTo.CUSTOMER_NOTE}, "no email body exists"),
    ],
)
def test_a_wrong_registration_is_refused(scratch_registry, overrides, message):
    with pytest.raises(MessageTypeRegistrationError, match=message):
        register_message_type(_type(**overrides))
    assert get_message_type(overrides.get("code", "test_offer_copy")) is None


def test_the_registry_hands_out_frozen_values(scratch_registry):
    codes = {MessageChannel.EMAIL: "email.offer_sent"}
    register_message_type(_type(text_codes=codes))
    codes[MessageChannel.SMS] = "sms.offer_sent"
    stored = get_message_type("test_offer_copy")
    assert stored.channels == (MessageChannel.EMAIL,)
    with pytest.raises(TypeError):
        stored.text_codes[MessageChannel.SMS] = "sms.offer_sent"  # type: ignore[index]
