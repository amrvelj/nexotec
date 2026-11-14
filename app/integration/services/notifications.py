"""Notification computation (WP-6 PR-6, ADR-025):

- Expiry warnings fire at exactly T-30/T-14/T-7 — never "at or before",
  which would double-fire on every day past a threshold once a job that
  missed a day catches up. Each is this context's record row plus the fact
  `integration.connection.expiry_approaching` on the outbox, committed
  together (rule 4); platform's consumer raises the `administration`
  notification to the Betrieb's managers in the app (KAN-222, FR-A-22,
  D-A-40: staff notifications are in-app and push, never e-mail). This
  context never calls `platform.public.notify` (rule 12, ADR-047).
  Platform-scope connections have no dealer to notify and are skipped.
- Break-glass access (api/call_payloads.py) notifies the dealer's manager
  every single time, with no daily batching — this is the one kind never
  deduplicated by day, since a second real access on the same day is a
  second real fact the manager should see.
- The daily support digest is exactly ONE row/ONE send per calendar day,
  aggregating every expiry warning that fired today
  — "never per-event" (ADR-025's own phrasing) is enforced by checking
  for an existing digest row for today before ever composing one.

Deduplication is a query-then-insert check, not a DB constraint — see
`IntegrationNotification`'s own docstring for why a single constraint
shape can't fit all three `kind`s at once, and why that's an accepted,
precedented trade-off in this single-worker codebase.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.base import utcnow
from app.core.notifications import Notification, get_notification_sender
from app.integration.models.connection import IntegrationConnection
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.integration.models.provider import IntegrationProvider
from app.platform.public import list_dealer_manager_emails

EXPIRY_WARNING_THRESHOLDS_DAYS = (30, 14, 7)

# KAN-222: the fact platform turns into the managers' `administration`
# notification (`app/platform/consumers.py`).
EXPIRY_APPROACHING_EVENT = "integration.connection.expiry_approaching"
PRODUCER = "integration"

# What the record row names as its recipient: the managers are resolved by
# platform's consumer, not here.
_ADMINISTRATION_RECIPIENT = "administration notification"

# Placeholder — which real inbox "Nexotec support" resolves to is a
# genuine follow-on decision (see app/core/notifications.py's own
# docstring: no real email provider is wired up in this codebase at
# all yet), not something this session can source correctly.
_SUPPORT_DIGEST_RECIPIENT = "support@nexotec.internal"


def _expiry_warning_already_sent(db: Session, *, connection_id: uuid.UUID, threshold_days: int, sent_date: dt.date) -> bool:
    return (
        db.scalar(
            select(IntegrationNotification.id).where(
                IntegrationNotification.connection_id == connection_id,
                IntegrationNotification.kind == NotificationKind.EXPIRY_WARNING,
                IntegrationNotification.threshold_days == threshold_days,
                IntegrationNotification.sent_date == sent_date,
            )
        )
        is not None
    )


def _send_and_record(
    db: Session, *, connection: IntegrationConnection | None, tenant_id: uuid.UUID | None, kind: NotificationKind,
    threshold_days: int | None, sent_date: dt.date, subject: str, summary: str, recipients: list[str],
) -> IntegrationNotification:
    sender = get_notification_sender()
    for recipient in recipients:
        sender.send(Notification(recipient=recipient, subject=subject, body=summary))
    row = IntegrationNotification(
        connection_id=connection.id if connection is not None else None,
        tenant_id=tenant_id,
        kind=kind,
        threshold_days=threshold_days,
        sent_date=sent_date,
        recipient=", ".join(recipients) if recipients else "(no active manager)",
        summary=summary,
    )
    db.add(row)
    db.commit()
    return row


def check_and_send_expiry_warnings(db: Session, *, today: dt.date | None = None) -> list[IntegrationNotification]:
    today = today or utcnow().date()
    sent: list[IntegrationNotification] = []
    connections = db.scalars(
        select(IntegrationConnection).where(
            IntegrationConnection.expires_at.is_not(None), IntegrationConnection.enabled.is_(True)
        )
    ).all()
    for connection in connections:
        if connection.tenant_id is None:
            continue  # platform-scope — no dealer manager to notify
        if connection.expires_at is None:  # SQL-filtered above; narrows the type for mypy
            continue
        days_remaining = (connection.expires_at.date() - today).days
        if days_remaining not in EXPIRY_WARNING_THRESHOLDS_DAYS:
            continue
        if _expiry_warning_already_sent(db, connection_id=connection.id, threshold_days=days_remaining, sent_date=today):
            continue
        sent.append(_record_expiry_approaching(db, connection=connection, days_remaining=days_remaining, today=today))
    return sent


def _record_expiry_approaching(
    db: Session, *, connection: IntegrationConnection, days_remaining: int, today: dt.date
) -> IntegrationNotification:
    """The warning's record and its fact, in one local transaction (rule 4).
    The payload names ids, the provider's code and catalogue name and the threshold only — the
    consumer reads nothing else and renders the text in each manager's
    language."""

    assert connection.tenant_id is not None  # platform-scope rows were skipped
    provider_code, provider_name = db.execute(
        select(IntegrationProvider.provider_code, IntegrationProvider.display_name).where(
            IntegrationProvider.id == connection.provider_id
        )
    ).one()
    row = IntegrationNotification(
        connection_id=connection.id,
        tenant_id=connection.tenant_id,
        kind=NotificationKind.EXPIRY_WARNING,
        threshold_days=days_remaining,
        sent_date=today,
        recipient=_ADMINISTRATION_RECIPIENT,
        summary=f"Connection '{connection.display_name}' expires in {days_remaining} day(s).",
    )
    db.add(row)
    db.flush()
    outbox.publish(
        db,
        outbox.OutboxEvent(
            event_type=EXPIRY_APPROACHING_EVENT,
            tenant_id=connection.tenant_id,
            producer=PRODUCER,
            aggregate_type="integration_connection",
            aggregate_id=connection.id,
            payload={
                "connectionId": str(connection.id),
                "dealershipId": str(connection.tenant_id),
                "providerCode": provider_code,
                # The catalogue's name for the provider, so the managers' notice
                # says which connection expires (Nexotec's text, not a dealer's).
                "providerName": provider_name,
                "thresholdDays": days_remaining,
                "expiresAt": connection.expires_at.isoformat() if connection.expires_at is not None else None,
            },
        ),
    )
    db.commit()
    return row


def notify_break_glass_access(
    db: Session, *, connection: IntegrationConnection, actor_id: uuid.UUID, reason: str
) -> IntegrationNotification | None:
    """Called by the break-glass read endpoint itself (api/
    call_payloads.py), after the audit event is already recorded — never
    deduplicated by day, since every real access is its own fact.
    Returns `None` for a platform-scope connection (no dealer manager
    exists to notify) rather than raising, so a platform_admin's
    break-glass read of a platform-scope connection's own payload is
    never blocked by a notification step that has nowhere to send to.
    """

    return _notify_break_glass(
        db, connection=connection, tenant_id=connection.tenant_id, connection_name=connection.display_name,
        reason=reason,
    )


def notify_break_glass_access_for_deleted_connection(
    db: Session, *, tenant_id: uuid.UUID | None, connection_name: str | None, actor_id: uuid.UUID, reason: str
) -> IntegrationNotification | None:
    """The same notice for a payload whose connection was deleted since
    the call (KAN-470: the call log and its payloads outlive it until
    ADR-024 retention purges them). The Betrieb comes from the call log
    and the name is the one it recorded at the time of the call; the row
    names no connection, since none exists any more."""

    return _notify_break_glass(
        db, connection=None, tenant_id=tenant_id, connection_name=connection_name or "(deleted)", reason=reason
    )


def _notify_break_glass(
    db: Session, *, connection: IntegrationConnection | None, tenant_id: uuid.UUID | None, connection_name: str,
    reason: str,
) -> IntegrationNotification | None:
    if tenant_id is None:
        return None
    summary = (
        f"A Nexotec platform administrator accessed raw integration data for connection "
        f"'{connection_name}'. Reason given: {reason}"
    )
    recipients = list_dealer_manager_emails(db, dealership_id=tenant_id)
    return _send_and_record(
        db, connection=connection, tenant_id=tenant_id, kind=NotificationKind.BREAK_GLASS_ACCESS,
        threshold_days=None, sent_date=utcnow().date(), subject="Integration data accessed by Nexotec support",
        summary=summary, recipients=recipients,
    )


def send_daily_support_digest(
    db: Session, *, today: dt.date | None = None, warnings: list[IntegrationNotification]
) -> IntegrationNotification | None:
    """ONE aggregated send to Nexotec support per day — never one per
    underlying warning. (It also counted the catalogue sync-age alarms
    until that sync was removed, 2026-10-29, KAN-485.) Returns `None` (and sends nothing) both
    when there is genuinely nothing to report AND when today's digest
    already went out (a restart mid-cycle re-running the daily job must
    never double-send).
    """

    today = today or utcnow().date()
    already_sent = (
        db.scalar(
            select(IntegrationNotification.id).where(
                IntegrationNotification.kind == NotificationKind.SUPPORT_DIGEST,
                IntegrationNotification.sent_date == today,
            )
        )
        is not None
    )
    if already_sent:
        return None
    if not warnings:
        return None

    summary = f"{len(warnings)} expiry warning(s) fired today across all tenants."
    return _send_and_record(
        db, connection=None, tenant_id=None, kind=NotificationKind.SUPPORT_DIGEST, threshold_days=None,
        sent_date=today, subject="Nexotec integrations daily digest", summary=summary,
        recipients=[_SUPPORT_DIGEST_RECIPIENT],
    )
