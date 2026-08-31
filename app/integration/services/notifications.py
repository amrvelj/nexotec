"""Notification computation (WP-6 PR-6, ADR-025):

- Expiry warnings fire at exactly T-30/T-14/T-7 — never "at or before",
  which would double-fire on every day past a threshold once a job that
  missed a day catches up. Each is an in-app record plus an email to
  every ACTIVE dealer manager on the connection's own tenant (platform-
  scope connections have no dealer to notify and are skipped).
- Break-glass access (api/call_payloads.py) notifies the dealer's manager
  every single time, with no daily batching — this is the one kind never
  deduplicated by day, since a second real access on the same day is a
  second real fact the manager should see.
- The daily support digest is exactly ONE row/ONE send per calendar day,
  aggregating every stale-sync alarm and expiry warning that fired today
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

from app.core.base import utcnow
from app.core.notifications import Notification, get_notification_sender
from app.integration.models.connection import IntegrationConnection
from app.integration.models.notification import IntegrationNotification, NotificationKind
from app.platform.public import list_dealer_manager_emails

EXPIRY_WARNING_THRESHOLDS_DAYS = (30, 14, 7)

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
        summary = f"Connection '{connection.display_name}' expires in {days_remaining} day(s)."
        recipients = list_dealer_manager_emails(db, dealership_id=connection.tenant_id)
        sent.append(
            _send_and_record(
                db, connection=connection, tenant_id=connection.tenant_id, kind=NotificationKind.EXPIRY_WARNING,
                threshold_days=days_remaining, sent_date=today, subject="Integration expiring soon", summary=summary,
                recipients=recipients,
            )
        )
    return sent


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

    if connection.tenant_id is None:
        return None
    summary = (
        f"A Nexotec platform administrator accessed raw integration data for connection "
        f"'{connection.display_name}'. Reason given: {reason}"
    )
    recipients = list_dealer_manager_emails(db, dealership_id=connection.tenant_id)
    return _send_and_record(
        db, connection=connection, tenant_id=connection.tenant_id, kind=NotificationKind.BREAK_GLASS_ACCESS,
        threshold_days=None, sent_date=utcnow().date(), subject="Integration data accessed by Nexotec support",
        summary=summary, recipients=recipients,
    )


def send_daily_support_digest(
    db: Session, *, today: dt.date | None = None, warnings: list[IntegrationNotification], alarms: list[uuid.UUID]
) -> IntegrationNotification | None:
    """ONE aggregated send to Nexotec support per day — never one per
    underlying warning/alarm. Returns `None` (and sends nothing) both
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
    if not warnings and not alarms:
        return None

    summary = (
        f"{len(warnings)} expiry warning(s) and {len(alarms)} sync-age alarm(s) fired today across all tenants."
    )
    return _send_and_record(
        db, connection=None, tenant_id=None, kind=NotificationKind.SUPPORT_DIGEST, threshold_days=None,
        sent_date=today, subject="Nexotec integrations daily digest", summary=summary,
        recipients=[_SUPPORT_DIGEST_RECIPIENT],
    )
