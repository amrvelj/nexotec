"""The two messaging provider adapters behind `MessagingGateway` (KAN-217
PR-3; Anto, 2026-11-14, Q2 B): Twilio for SMS and Meta's WhatsApp Cloud API
directly for WhatsApp, over `httpx`. Each is the Betrieb's own account (BYO).

One choke point per adapter, `_HttpMessagingAdapter._call`: one metered
request under the provider's own operation name (`messages_create`,
`account_fetch`; `messages`, `phone_number_fetch`), the HTTP status and the
provider's error **code** noted on that request's call-log row (KAN-767 —
Twilio's numeric `code`, Meta's `error.code`; never their `message`, which can
quote the recipient), and every failure translated into the
`app.integration.errors` vocabulary with words of ours:

- a refused credential (Twilio 401; Meta 401 or code 190) →
  `ProviderRejectedError` — the dealer's key, charged to the breaker of the
  dealer's own connection, as an AI key is;
- not allowed (403; Meta codes 10 and 200–299) → `ProviderNotEntitledError`;
- the request itself refused (400 / 404 / 422: a number the provider will not
  deliver to, a template that is not approved, Meta's 131047 outside the
  24-hour window) → `ProviderRequestError`, never charged to the breaker —
  one bad number must not switch the channel off;
- a rate limit (429; Meta codes 4, 80007, 130429, 131048, 131056) →
  `ProviderRejectedError`;
- 5xx, a transport failure → `ProviderTransportError`; a timeout →
  `ProviderTimeoutError`.

**No retry.** A message is billed and delivered once it is accepted; a second
attempt after a timeout may send it twice. `deliver_message` (PR-5) records
the failure and the user sends again.

**What never leaves the request:** the recipient's number and the text are
in the request body only — never in a log line, an exception message or the
call log. The credential is redacted the same way (the `Authorization`
header is never logged by this module).

The wire formats, checked against the providers' public documentation on
2026-11-21 (fake transports only — no real account was called in this change):

- Twilio: `POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json`,
  form-encoded `To`, `Body` and either `MessagingServiceSid` (an `MG…` sender
  configured at Twilio) or `From` (a number or an alphanumeric sender ID);
  HTTP Basic with an API key (`SK…` sid and its secret). The answer's `sid`
  (`SM…`) and `status` (`queued`, `accepted`). «Test connection»:
  `GET …/Accounts/{AccountSid}.json`.
- Meta WhatsApp Cloud API: `POST https://graph.facebook.com/{version}/{phone-number-id}/messages`,
  `Authorization: Bearer <token>`, JSON `messaging_product: whatsapp`,
  `recipient_type: individual`, `to`, and `type: template` with
  `template.name`, `template.language.code` and the body's text parameters, or
  `type: text` with `text.body`. The answer's `messages[0].id` (`wamid…`).
  «Test connection»: `GET …/{phone-number-id}?fields=display_phone_number,verified_name`.
"""

import re
import uuid
from collections.abc import Callable
from typing import Any, cast

import httpx
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.integration.adapters.messaging_base import (
    MESSAGING_PROVIDER_CODES,
    META_WHATSAPP,
    TWILIO,
    MessagingChannel,
    MessagingGateway,
    OutboundMessage,
    SentMessage,
    SmsMessage,
    WhatsAppTemplateMessage,
    WhatsAppTextMessage,
    check_message,
)
from app.integration.errors import (
    ProviderConfigurationError,
    ProviderNotEntitledError,
    ProviderRejectedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.metering import ProviderRequest, provider_request

TWILIO_ACCOUNT_SID_KEY = "accountSid"
TWILIO_API_KEY_SID_KEY = "apiKeySid"
TWILIO_SENDER_KEY = "sender"
META_PHONE_NUMBER_ID_KEY = "phoneNumberId"

_TWILIO_BASE = "https://api.twilio.com/2010-04-01"
# The Graph API version every Meta request names — one constant, raised on
# purpose (Meta retires a version about two years after its release).
META_GRAPH_VERSION = "v27.0"
_META_BASE = f"https://graph.facebook.com/{META_GRAPH_VERSION}"

# Every configured value lands in a URL path or a form field: each is checked
# for its documented shape before anything is sent (no path injection through
# the configuration).
_TWILIO_ACCOUNT_SID = re.compile(r"AC[0-9a-fA-F]{32}")
_TWILIO_API_KEY_SID = re.compile(r"SK[0-9a-fA-F]{32}")
_TWILIO_MESSAGING_SERVICE_SID = re.compile(r"MG[0-9a-fA-F]{32}")
_E164_SENDER = re.compile(r"\+[1-9]\d{6,14}")
# Twilio's alphanumeric sender ID: up to 11 letters, digits and spaces, with
# at least one letter.
_ALPHANUMERIC_SENDER = re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9 ]{1,11}")
_META_PHONE_NUMBER_ID = re.compile(r"\d{1,20}")

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Meta's error codes (`error.code`), as its error reference groups them.
_META_TOKEN_REFUSED = frozenset({190})
_META_RATE_LIMITED = frozenset({4, 80007, 130429, 131048, 131056})
_META_OUTSIDE_WINDOW = 131047


def http_transport() -> httpx.BaseTransport | None:
    """The transport every adapter's client uses — None, httpx's own, in the
    application; a test replaces this function with a `MockTransport`."""

    return None


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TimeoutException, ProviderTimeoutError))


def _code(value: object) -> str | None:
    """A provider error code as the call log keeps it: an integer only."""

    return str(value) if isinstance(value, int) and not isinstance(value, bool) else None


class _HttpMessagingAdapter:
    provider_label = "The messaging provider"
    channel: MessagingChannel
    secret_slot: SecretSlot

    def __init__(
        self, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
    ) -> None:
        self._db = db
        self._connection = connection
        self._actor_id = actor_id
        self._purpose = purpose

    # -- configuration -----------------------------------------------------

    def _config(self, key: str, pattern: re.Pattern[str], shape: str) -> str:
        value = (self._connection.config or {}).get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(
                f"This connection has no '{key}' set — enter it in the connection's settings and test again."
            )
        value = value.strip()
        if pattern.fullmatch(value) is None:
            raise ProviderConfigurationError(f"The '{key}' of this connection is not {shape}.")
        return value

    def _secret(self) -> str:
        slot = self.secret_slot.value
        try:
            value = secrets_backend.resolve_secret(connection_id=self._connection.id, slot=slot)
        except Exception as exc:
            # Class name only: the secrets manager's message may name its path.
            raise ProviderConfigurationError(
                f"The '{slot}' credential of this connection could not be read ({type(exc).__name__}) "
                "— set it and test again."
            ) from exc
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=self._connection.id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={slot} purpose={self._purpose}",
        )
        return value

    # -- the one choke point -----------------------------------------------

    def _call(self, operation: str, send: Callable[[httpx.Client], httpx.Response]) -> dict[str, Any]:
        with provider_request(operation, is_timeout=_is_timeout) as request:
            response = request.attempt(lambda: self._send(request, send))
            return self._json(response)

    def _send(self, request: ProviderRequest, send: Callable[[httpx.Client], httpx.Response]) -> httpx.Response:
        try:
            with httpx.Client(timeout=_TIMEOUT, transport=http_transport()) as client:
                response = send(client)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.provider_label} did not answer in time ({type(exc).__name__}).") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"{self.provider_label} could not be reached ({type(exc).__name__}).") from exc
        code = self._error_code(response) if response.status_code >= 400 else None
        request.note_response(response.status_code, provider_error=code)
        self._raise_for_status(response.status_code, code)
        return response

    def _error_body(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _error_code(self, response: httpx.Response) -> str | None:
        raise NotImplementedError

    def _raise_for_status(self, status: int, code: str | None) -> None:
        raise NotImplementedError

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderTransportError(f"{self.provider_label} sent an answer that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderTransportError(f"{self.provider_label} sent an answer of an unexpected shape.")
        return body

    def _refuse_other_channel(self, message: OutboundMessage) -> None:
        check_message(message)
        if message.channel != self.channel:
            raise TypeError(f"A {self.provider_label} connection sends {self.channel.value} messages only.")


class TwilioSmsAdapter(_HttpMessagingAdapter):
    provider_label = "Twilio"
    channel = MessagingChannel.SMS
    # The API key's secret; the key's `SK…` sid is configuration.
    secret_slot = SecretSlot.API_KEY

    def _account_sid(self) -> str:
        return self._config(TWILIO_ACCOUNT_SID_KEY, _TWILIO_ACCOUNT_SID, "a Twilio account SID (AC…)")

    def _auth(self) -> tuple[str, str]:
        key_sid = self._config(TWILIO_API_KEY_SID_KEY, _TWILIO_API_KEY_SID, "a Twilio API key SID (SK…)")
        return key_sid, self._secret()

    def _sender_field(self) -> tuple[str, str]:
        value = (self._connection.config or {}).get(TWILIO_SENDER_KEY)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(
                f"This connection has no '{TWILIO_SENDER_KEY}' set — enter it in the connection's settings and test again."
            )
        value = value.strip()
        if _TWILIO_MESSAGING_SERVICE_SID.fullmatch(value):
            return "MessagingServiceSid", value
        if _E164_SENDER.fullmatch(value) or _ALPHANUMERIC_SENDER.fullmatch(value):
            return "From", value
        raise ProviderConfigurationError(
            f"The '{TWILIO_SENDER_KEY}' of this connection is not a Messaging Service SID (MG…), "
            "a number (+41…) or a sender name of at most 11 letters and digits."
        )

    def _error_code(self, response: httpx.Response) -> str | None:
        return _code(self._error_body(response).get("code"))

    def _raise_for_status(self, status: int, code: str | None) -> None:
        if status < 400:
            return
        if status == 401:
            raise ProviderRejectedError("Twilio refused the API key (HTTP 401) — check the key's SID and secret.")
        if status == 403:
            raise ProviderNotEntitledError(
                "messages_create", "Twilio: the account may not send this message (HTTP 403)."
            )
        if status in (400, 404, 422):
            raise ProviderRequestError(
                f"Twilio refused the message (HTTP {status}"
                + (f", code {code}" if code else "")
                + ") — check the recipient's number and the sender."
            )
        if status == 429:
            raise ProviderRejectedError("Twilio refused the message (HTTP 429): the account's rate limit is reached.")
        raise ProviderTransportError(f"Twilio failed to answer (HTTP {status}).")

    def send(self, message: OutboundMessage) -> SentMessage:
        self._refuse_other_channel(message)
        sms = cast(SmsMessage, message)
        account_sid = self._account_sid()
        sender_field, sender = self._sender_field()
        auth = self._auth()
        url = f"{_TWILIO_BASE}/Accounts/{account_sid}/Messages.json"
        form = {"To": sms.to, sender_field: sender, "Body": sms.body}
        body = self._call("messages_create", lambda client: client.post(url, data=form, auth=auth))
        sid = body.get("sid")
        if not isinstance(sid, str) or not sid:
            raise ProviderTransportError("Twilio accepted the message without naming it.")
        status = body.get("status")
        return SentMessage(provider_message_id=sid, provider_status=status if isinstance(status, str) else None)

    def verify(self) -> None:
        account_sid = self._account_sid()
        self._sender_field()  # a sender that could never send fails the test, not the first message
        auth = self._auth()
        url = f"{_TWILIO_BASE}/Accounts/{account_sid}.json"
        self._call("account_fetch", lambda client: client.get(url, auth=auth))


class MetaWhatsAppAdapter(_HttpMessagingAdapter):
    provider_label = "WhatsApp (Meta)"
    channel = MessagingChannel.WHATSAPP
    # A system user's permanent access token at Meta.
    secret_slot = SecretSlot.ACCESS_TOKEN

    def _phone_number_id(self) -> str:
        return self._config(META_PHONE_NUMBER_ID_KEY, _META_PHONE_NUMBER_ID, "a WhatsApp phone number ID (digits)")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret()}"}

    def _error_code(self, response: httpx.Response) -> str | None:
        error = self._error_body(response).get("error")
        return _code(error.get("code")) if isinstance(error, dict) else None

    def _raise_for_status(self, status: int, code: str | None) -> None:
        if status < 400:
            return
        numeric = int(code) if code is not None else None
        if status == 401 or numeric in _META_TOKEN_REFUSED:
            raise ProviderRejectedError("Meta refused the access token — check the token and test again.")
        if status == 429 or numeric in _META_RATE_LIMITED:
            raise ProviderRejectedError("Meta refused the message: the account's rate limit is reached.")
        if numeric == _META_OUTSIDE_WINDOW:
            raise ProviderRequestError(
                "Meta refused the message: outside the 24-hour window only an approved template may be sent."
            )
        if status == 403 or (numeric is not None and (numeric == 10 or 200 <= numeric <= 299)):
            raise ProviderNotEntitledError("messages", "Meta: the token may not send from this phone number.")
        if status in (400, 404, 422):
            raise ProviderRequestError(
                f"Meta refused the message (HTTP {status}"
                + (f", code {code}" if code else "")
                + ") — check the recipient's number and the template."
            )
        raise ProviderTransportError(f"Meta failed to answer (HTTP {status}).")

    def _payload(self, message: OutboundMessage) -> dict[str, Any]:
        base: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.to,
        }
        if isinstance(message, WhatsAppTemplateMessage):
            template: dict[str, Any] = {"name": message.template_name, "language": {"code": message.language}}
            if message.body_parameters:
                template["components"] = [
                    {"type": "body", "parameters": [{"type": "text", "text": value} for value in message.body_parameters]}
                ]
            return {**base, "type": "template", "template": template}
        text = cast(WhatsAppTextMessage, message)
        return {**base, "type": "text", "text": {"preview_url": False, "body": text.body}}

    def send(self, message: OutboundMessage) -> SentMessage:
        self._refuse_other_channel(message)
        payload = self._payload(message)
        url = f"{_META_BASE}/{self._phone_number_id()}/messages"
        headers = self._headers()
        body = self._call("messages", lambda client: client.post(url, json=payload, headers=headers))
        messages = body.get("messages")
        first = messages[0] if isinstance(messages, list) and messages else None
        message_id = first.get("id") if isinstance(first, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise ProviderTransportError("Meta accepted the message without naming it.")
        status = first.get("message_status") if isinstance(first, dict) else None
        return SentMessage(provider_message_id=message_id, provider_status=status if isinstance(status, str) else None)

    def verify(self) -> None:
        url = f"{_META_BASE}/{self._phone_number_id()}"
        headers = self._headers()
        self._call(
            "phone_number_fetch",
            lambda client: client.get(url, params={"fields": "display_phone_number,verified_name"}, headers=headers),
        )


_ADAPTERS: dict[str, type[TwilioSmsAdapter] | type[MetaWhatsAppAdapter]] = {TWILIO: TwilioSmsAdapter, META_WHATSAPP: MetaWhatsAppAdapter}


def build_adapter(
    provider_code: str, db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> MessagingGateway:
    """Builds no network connection and resolves no secret: both happen on
    the first request, inside the caller's `call_capability` block."""

    return _ADAPTERS[provider_code](db, connection, actor_id, purpose)


def _register() -> None:
    # Imported lazily, as the other adapters do: nothing in adapters/ imports
    # gateway.py at module scope. The registry's factory type names
    # ProviderAdapter; it is polymorphic at runtime, and
    # `services/messaging.py` casts back to MessagingGateway.
    from app.integration.adapters.base import ProviderAdapter
    from app.integration.services.gateway import register_adapter_factory

    for code in MESSAGING_PROVIDER_CODES:
        factory = cast(
            Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter],
            lambda db, connection, actor_id, purpose, code=code: build_adapter(code, db, connection, actor_id, purpose),
        )
        register_adapter_factory(code, factory)


_register()
