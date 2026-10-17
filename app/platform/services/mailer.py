"""The platform sender seam (FR-AU-15, D-A-28, KAN-196; Anto, 2026-10-17):
invitations and resets leave through a Nexotec-owned mailbox, never a
dealer's messaging connection. Two senders: `LoggingMailer` writes the
message to the application log (dev, test, every environment without a
mailbox yet) and `SmtpMailer` sends it through the configured mailbox with
the standard library. Delivery is not fire-and-forget: a failure is raised
to the caller, which records a `mail_delivery_failed` auth event for
platform staff and logs it at ERROR — a dealer whose invitations never
arrive is a tenant that silently never starts."""

import dataclasses
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


class MailDeliveryError(Exception):
    """The platform sender could not hand the message over."""


@dataclasses.dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str


class PlatformMailer(Protocol):
    def send(self, mail: Mail) -> None: ...


class LoggingMailer:
    """Writes the whole message to the log — the link included, which is
    how a developer or a test gets at it. Never used where a real mailbox is
    configured."""

    def send(self, mail: Mail) -> None:
        logger.info("platform_mail", extra={"to": mail.to, "subject": mail.subject, "text": mail.text})


class SmtpMailer:
    def send(self, mail: Mail) -> None:
        message = EmailMessage()
        message["From"] = _settings.platform_mail_from
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message.set_content(mail.text)
        try:
            with smtplib.SMTP(_settings.platform_mail_smtp_host, _settings.platform_mail_smtp_port, timeout=10) as smtp:
                if _settings.platform_mail_smtp_starttls:
                    smtp.starttls()
                if _settings.platform_mail_smtp_username:
                    smtp.login(_settings.platform_mail_smtp_username, _settings.platform_mail_smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailDeliveryError(str(exc)) from exc


_mailer: PlatformMailer | None = None


def get_mailer() -> PlatformMailer:
    """FastAPI dependency; tests override it with a recording double."""

    global _mailer
    if _mailer is None:
        if _settings.platform_mail_sender != "smtp" and _settings.environment == "production":
            # The logging sender writes live reset and invitation links to
            # the application log — a dev and staging convenience, never
            # production's sender (CLAUDE.md: secrets are never logged).
            raise RuntimeError("DMS_PLATFORM_MAIL_SENDER must be 'smtp' in production.")
        _mailer = SmtpMailer() if _settings.platform_mail_sender == "smtp" else LoggingMailer()
    return _mailer
