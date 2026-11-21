"""MessagingGateway (KAN-217 PR-3, D-A-41, FR-A-51–54) — the shape every SMS
and WhatsApp provider adapter implements: one message out, the provider's id
for it and its first status back.

**Bring your own provider** (D-A-41; Anto, 2026-11-14, Q2 B): the Betrieb
connects its own account — Twilio for SMS, Meta's WhatsApp Cloud API directly
for WhatsApp. Each is a *tenant* connection of category `messaging`; Nexotec
holds no messaging account of its own (`connections.TENANT_ONLY_PROVIDER_CODES`
refuses a platform-scope one). The credential is a secret slot resolved in
memory per request like every credential.

A fourth Protocol beside `ProviderAdapter` (vehicle data), `MarketplaceAdapter`
(outbound feed) and `AiGateway`/`MailGateway`, behind the same
`services/gateway.py::call_capability`: credential resolution, the circuit
breaker and one `integration_call_log` row per request. A row carries the
operation, the HTTP status and the provider's error *code* — never the
recipient's number, never the text (they are personal data and stay out of
the call log).

Nothing here decides *whether* a message may be sent: consent, the WhatsApp
template rule and the delivery record are `deliver_message`'s (PR-5), which
calls `services/messaging.py::send` with a verdict already taken from
`customer.public`. No module outside integration reaches an adapter
(A-RULE-10).
"""

import enum
import re
from dataclasses import dataclass, field
from typing import Protocol

MESSAGING_CATEGORY = "messaging"
# The one call label a messaging connection is used under; also the provider
# rows' `capability_codes` entry.
MESSAGING_SEND_CAPABILITY = "messaging_send"

# E.164: a plus, a country code that does not start with 0, at most 15 digits.
_E164 = re.compile(r"\+[1-9]\d{6,14}")


class MessagingChannel(str, enum.Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"


# The provider codes (catalogue rows of integration migration `c7d2a9e4f1b8`),
# one per channel.
TWILIO = "twilio"
META_WHATSAPP = "meta_whatsapp"
MESSAGING_PROVIDER_CODES = (TWILIO, META_WHATSAPP)
CHANNEL_PROVIDER: dict[MessagingChannel, str] = {MessagingChannel.SMS: TWILIO, MessagingChannel.WHATSAPP: META_WHATSAPP}


def require_e164(number: str) -> str:
    """The recipient's number as the providers take it, or a ValueError — a
    caller hands a number already normalised by Customers (ADR-067); anything
    else is a programming error, never sent."""

    if not isinstance(number, str) or _E164.fullmatch(number) is None:
        raise ValueError("A messaging recipient is an E.164 number (+41…).")
    return number


@dataclass(frozen=True)
class SmsMessage:
    to: str
    body: str
    channel: MessagingChannel = field(default=MessagingChannel.SMS, init=False)


@dataclass(frozen=True)
class WhatsAppTemplateMessage:
    """An approved template (the only kind Meta delivers outside the 24-hour
    customer-service window): its name and language as approved at Meta,
    and the body's `{{1}}`, `{{2}}` … values in order."""

    to: str
    template_name: str
    language: str
    body_parameters: tuple[str, ...] = ()
    channel: MessagingChannel = field(default=MessagingChannel.WHATSAPP, init=False)


@dataclass(frozen=True)
class WhatsAppTextMessage:
    """Free text — Meta delivers it only inside the customer-service window."""

    to: str
    body: str
    channel: MessagingChannel = field(default=MessagingChannel.WHATSAPP, init=False)


OutboundMessage = SmsMessage | WhatsAppTemplateMessage | WhatsAppTextMessage

# Twilio's own limit on one message's body; Meta's on a text message's.
SMS_BODY_MAX = 1600
WHATSAPP_TEXT_MAX = 4096
# A template name as Meta accepts it, and its language code (de, de_CH).
_TEMPLATE_NAME = re.compile(r"[a-z0-9_]{1,512}")
_TEMPLATE_LANGUAGE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")


def check_message(message: OutboundMessage) -> OutboundMessage:
    """The message's shape, checked before any provider is called: a number
    that is not E.164, an empty or too long text, a template name or
    language Meta would not accept is a ValueError — a programming error in
    the caller, never sent, never logged and never charged to the breaker."""

    require_e164(message.to)
    if isinstance(message, SmsMessage):
        if not message.body or len(message.body) > SMS_BODY_MAX:
            raise ValueError(f"An SMS body holds 1 to {SMS_BODY_MAX} characters.")
    elif isinstance(message, WhatsAppTextMessage):
        if not message.body or len(message.body) > WHATSAPP_TEXT_MAX:
            raise ValueError(f"A WhatsApp text holds 1 to {WHATSAPP_TEXT_MAX} characters.")
    elif isinstance(message, WhatsAppTemplateMessage):
        if _TEMPLATE_NAME.fullmatch(message.template_name) is None:
            raise ValueError("A WhatsApp template name holds lower-case letters, digits and underscores.")
        if _TEMPLATE_LANGUAGE.fullmatch(message.language) is None:
            raise ValueError("A WhatsApp template language is a code such as de or de_CH.")
        if any(not isinstance(value, str) or not value for value in message.body_parameters):
            raise ValueError("A WhatsApp template parameter is a non-empty text.")
    else:
        raise TypeError(f"Not a message: {type(message).__name__}.")
    return message


@dataclass(frozen=True)
class SentMessage:
    """What the provider answered when it accepted the message: its own id
    (Twilio's `SM…` sid, Meta's `wamid…`) and its first status, as it words
    it (`queued`, `accepted` …; None when it states none). Delivery itself is
    reported later by the provider's status callbacks (PR-5)."""

    provider_message_id: str
    provider_status: str | None


class MessagingGateway(Protocol):
    channel: MessagingChannel

    def send(self, message: OutboundMessage) -> SentMessage:
        """One message. Raises the `app.integration.errors` vocabulary,
        never a client library's exception; a message of another channel is a
        `TypeError` (a programming error, never sent)."""
        ...

    def verify(self) -> None:
        """«Test connection»: one read of the account (a one-row page of
        Twilio's messages — a Standard API key may not read the Accounts
        resource; Meta's phone number) that proves the credential and the sender together and
        sends nothing."""
        ...
