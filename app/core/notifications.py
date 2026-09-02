"""A generic outbound-notification seam (WP-6 PR-6) — cross-cutting since
nothing about it is integration-specific, even though its first caller is
`app.integration.services.notifications`. No email-sending or in-app
notification/inbox infrastructure exists anywhere in this codebase
(grepped exhaustively for smtp/sendgrid/postmark/send_email — nothing).
Building a real provider is a genuine follow-on, not this PR's job — the
same "ship the seam, defer the real provider" posture PR-2's mock adapter
already uses. `LoggingNotificationSender` is the only implementation
today; wiring a real one later (SendGrid, SES, whatever gets chosen)
means implementing this one Protocol and swapping the instance app-wide,
never touching a caller.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("app.core.notifications")


@dataclass(frozen=True)
class Notification:
    recipient: str
    subject: str
    body: str


class NotificationSender(Protocol):
    def send(self, notification: Notification) -> None: ...


class LoggingNotificationSender:
    """Logs at WARNING (a notification that never actually reached anyone
    is an operational fact worth seeing in the logs, not a DEBUG-level
    footnote) — every call site already tolerates this being the whole
    delivery mechanism today.
    """

    def send(self, notification: Notification) -> None:
        logger.warning(
            "notification (no real provider configured)",
            extra={"recipient": notification.recipient, "subject": notification.subject, "body": notification.body},
        )


_default_sender: NotificationSender = LoggingNotificationSender()


def get_notification_sender() -> NotificationSender:
    return _default_sender


def set_notification_sender(sender: NotificationSender) -> None:
    """Test-only override point, and the one line a real provider's own
    wiring module would call at startup once one exists."""

    global _default_sender
    _default_sender = sender
