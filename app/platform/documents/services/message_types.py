"""The registry of customer message types (KAN-217 PR-1; Dealer Administration
D-A-41, FR-A-52, FR-A-53, A-RULE-10).

The send dialog is the platform's; the modules own what is sent. A module
registers each **document type** (an offer, an order confirmation, a sales
contract …) and **reminder type** (an appointment reminder, «vehicle ready»
…) it lets a user send to a customer, and the dialog — and the send service
behind it (later PRs) — read everything type-specific from here:

- **purpose** — transactional or marketing (FR-A-53): a property of the type,
  never a choice at send time. Transactional needs a usable contact row on the
  channel; marketing keeps D-17's three consent gates (Anto, 2026-11-14, Q1 A).
- **whether it carries a document** — a document type has a rendered PDF; a
  reminder has none. The PDF travels as an attachment where the channel
  carries one (e-mail) and as a secure link where it does not (SMS, WhatsApp)
  — `delivery_of`.
- **the standard text per channel** — one code per channel the type is sent
  on; the dialog loads that code in the customer's correspondence language.
  Every code must be a valid message body for the type (its category is the
  channel's, its target exists in the closed placeholder registry) and must
  ship as a platform seed in all four languages
  (`tests/platform/test_kan217_message_types.py`).

**Registered at start-up, by the module.** Platform is the lowest layer and
never imports a module above it (ADR-131), so the modules call
`register_message_type` from the composition root `app/message_type_registry.py`
(imported by `app.main` and `app.worker`), as the status providers are
registered. Registering the same definition twice is harmless; a second,
different definition of a code is refused at start-up, since two modules
claiming one type is a defect, not a configuration.

Sales registers `offer`, `order_confirmation` and `sales_contract` (this
ticket); Finance, Aftersales and the rest register theirs when they are built.
"""

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.platform.documents.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.documents.services.standard_text_placeholders import target_for
from app.platform.identity.models.standard_text_delegation import StandardTextCategory


class MessagePurpose(str, enum.Enum):
    """FR-A-53: what consent a send of this type needs."""

    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"


class MessageTypeKind(str, enum.Enum):
    """FR-A-52: a document a module renders, or a reminder without one."""

    DOCUMENT = "document"
    REMINDER = "reminder"


class MessageChannel(str, enum.Enum):
    """FR-A-51: the send dialog's three channels."""

    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class MessageDelivery(str, enum.Enum):
    """How a send carries its document on a channel (FR-A-52)."""

    ATTACHMENT = "attachment"
    SECURE_LINK = "secure_link"
    # A reminder carries no document: the text is the whole message.
    TEXT_ONLY = "text_only"


# The standard-text category each channel's bodies are written in.
CHANNEL_TEXT_CATEGORY: Mapping[MessageChannel, StandardTextCategory] = MappingProxyType(
    {
        MessageChannel.EMAIL: StandardTextCategory.EMAIL,
        MessageChannel.SMS: StandardTextCategory.SMS,
        MessageChannel.WHATSAPP: StandardTextCategory.WHATSAPP,
    }
)

def text_category_of(channel: MessageChannel) -> StandardTextCategory:
    """The standard-text category a channel's bodies are written in."""

    return CHANNEL_TEXT_CATEGORY[MessageChannel(channel)]


# The channels that carry an attachment. SMS and WhatsApp send a secure link
# (Anto's smaller points, 2026-11-14: «E-mail attaches the PDF. SMS and
# WhatsApp send a secure link»).
ATTACHMENT_CHANNELS: frozenset[MessageChannel] = frozenset({MessageChannel.EMAIL})

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


class MessageTypeRegistrationError(ValueError):
    """A registration that would make the registry wrong — raised at start-up."""


@dataclass(frozen=True)
class MessageType:
    code: str
    # The context that owns the document or reminder (`sales`, `aftersales` …).
    module: str
    kind: MessageTypeKind
    purpose: MessagePurpose
    # Where its texts are used in the standard-text library (`applies_to`).
    applies_to: StandardTextAppliesTo
    # The seed code of the body per channel it is sent on.
    text_codes: Mapping[MessageChannel, str] = field(default_factory=dict)

    @property
    def carries_document(self) -> bool:
        return self.kind == MessageTypeKind.DOCUMENT

    @property
    def channels(self) -> tuple[MessageChannel, ...]:
        return tuple(channel for channel in MessageChannel if channel in self.text_codes)


def delivery_of(message_type: MessageType, channel: MessageChannel) -> MessageDelivery:
    """How a send of this type carries its document on this channel."""

    if not message_type.carries_document:
        return MessageDelivery.TEXT_ONLY
    return MessageDelivery.ATTACHMENT if channel in ATTACHMENT_CHANNELS else MessageDelivery.SECURE_LINK


_REGISTRY: dict[str, MessageType] = {}


def _check(message_type: MessageType) -> None:
    if not _CODE.fullmatch(message_type.code):
        raise MessageTypeRegistrationError(f"Message type code {message_type.code!r} is not a lower-case identifier.")
    if not message_type.module:
        raise MessageTypeRegistrationError(f"Message type {message_type.code!r} names no owning module.")
    if not message_type.text_codes:
        raise MessageTypeRegistrationError(f"Message type {message_type.code!r} is sent on no channel.")
    for channel, text_code in message_type.text_codes.items():
        category = CHANNEL_TEXT_CATEGORY[MessageChannel(channel)]
        if not text_code.startswith(f"{category.value}."):
            raise MessageTypeRegistrationError(
                f"Message type {message_type.code!r}: the {channel.value} text {text_code!r} is not a "
                f"{category.value} body (its code must start with {category.value!r}.)."
            )
        target = target_for(category, message_type.applies_to)
        if target is None or StandardTextSlot.BODY not in target.slots:
            raise MessageTypeRegistrationError(
                f"Message type {message_type.code!r}: no {category.value} body exists for "
                f"{message_type.applies_to.value!r} in the placeholder registry."
            )


def register_message_type(message_type: MessageType) -> None:
    """Adds a type to the registry; the composition root calls it for each
    module at start-up. The same definition again is a no-op."""

    _check(message_type)
    frozen = MessageType(
        code=message_type.code,
        module=message_type.module,
        kind=message_type.kind,
        purpose=message_type.purpose,
        applies_to=message_type.applies_to,
        text_codes=MappingProxyType(dict(message_type.text_codes)),
    )
    existing = _REGISTRY.get(frozen.code)
    if existing is not None:
        if _same(existing, frozen):
            return
        raise MessageTypeRegistrationError(
            f"Message type {frozen.code!r} is already registered by {existing.module!r} with another definition."
        )
    _REGISTRY[frozen.code] = frozen


def _same(a: MessageType, b: MessageType) -> bool:
    return (a.code, a.module, a.kind, a.purpose, a.applies_to, dict(a.text_codes)) == (
        b.code,
        b.module,
        b.kind,
        b.purpose,
        b.applies_to,
        dict(b.text_codes),
    )


def message_types() -> tuple[MessageType, ...]:
    """Every registered type, ordered by code."""

    return tuple(_REGISTRY[code] for code in sorted(_REGISTRY))


def get_message_type(code: str) -> MessageType | None:
    return _REGISTRY.get(code)
