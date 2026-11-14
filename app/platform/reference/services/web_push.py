"""The Web Push delivery port (KAN-216; Dealer Administration FR-A-49).

A push is a **pointer to the inbox** (FR-A-50): it carries the title and the
SPA link, nothing else — never the body, never a parameter. The sender
encrypts it to the browser's keys (RFC 8291) and signs it with the
platform's VAPID key (RFC 8292).

What sends it is a port, `WebPushSender`, chosen once per process:

- `UnconfiguredWebPushSender` — the default, and the only adapter built.
  The platform holds no VAPID key yet: where its settings belong is a
  decision for Anto (`app/core/config.py` is a preserved file; see the
  KAN-216 report). It sends nothing, says so per call (`NOT_CONFIGURED`),
  and the SPA is told push is unavailable (`vapid_public_key()` is None), so
  no browser is asked for permission it cannot use.
- A VAPID adapter registers itself with `register_web_push_sender` once the
  key's home is decided; nothing else changes — the consumer, the
  subscriptions and the SPA's service worker are built against this port.

Never an e-mail, whatever the outcome (D-A-40).
"""

import dataclasses
import enum
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class PushOutcome(str, enum.Enum):
    DELIVERED = "delivered"
    # 404/410 from the push service: the browser dropped the subscription.
    # The row is deleted.
    GONE = "gone"
    # Any other refusal or a network failure: the consumer raises and the
    # outbox retries the message (at-least-once; a push is idempotent enough
    # — the user sees the same title twice at worst, the inbox once).
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclasses.dataclass(frozen=True)
class PushTarget:
    endpoint: str
    p256dh: str
    auth: str


@dataclasses.dataclass(frozen=True)
class PushMessage:
    """Title and link only (FR-A-50, the ticket: no content beyond them)."""

    title: str
    link: str

    def as_payload(self) -> dict[str, str]:
        return {"title": self.title, "link": self.link}


class WebPushSender(Protocol):
    def public_key(self) -> str | None:
        """The VAPID application server key the browser subscribes with
        (base64url), or None when push cannot be sent at all."""

    def send(self, target: PushTarget, message: PushMessage) -> PushOutcome: ...


class UnconfiguredWebPushSender:
    def public_key(self) -> str | None:
        return None

    def send(self, target: PushTarget, message: PushMessage) -> PushOutcome:
        # Never the endpoint (a capability URL) nor the title in the log.
        logger.info("web_push_not_configured")
        return PushOutcome.NOT_CONFIGURED


_sender: WebPushSender = UnconfiguredWebPushSender()


def get_web_push_sender() -> WebPushSender:
    return _sender


def register_web_push_sender(sender: WebPushSender) -> None:
    """Once, at start-up (or by a test)."""

    global _sender
    _sender = sender


def reset_web_push_sender() -> None:
    global _sender
    _sender = UnconfiguredWebPushSender()


def vapid_public_key() -> str | None:
    return _sender.public_key()
