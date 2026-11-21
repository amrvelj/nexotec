"""The status page's notifications (KAN-223; D-A-38, D-A-40's
`administration` family).

The daily job `platform.status_page.notifications` derives the page of every
active Betrieb and compares each item with its `status_item_state` row. A
degradation is announced to the Betrieb's active managers **exactly once, by
someone**:

- an item that is warning or blocked and was ok (or never seen) raises one
  `administration.status_item_degraded` notification to each active manager
  — or the item's own one when it names one this module knows
  (`StatusItem.notification_event`; Google Business Profile's
  `administration.business_profile_interrupted`, KAN-776) — and the row
  records when (`notified_at`);
- a warning its source really announced itself (`notified_by_source`: an
  integration whose expiry notice was sent, KAN-222) is recorded, not
  notified a second time — but the move on to **blocked** is never exempt: if
  nobody here told the managers yet, blocked does;
- otherwise, while an item stays degraded nothing more is raised; an item ok
  again sets its row to ok, so a later degradation is a new one;
- an item that is gone (a connection deleted, an incident over) drops its row
  — except while its source cannot be asked: a source that raised has its
  items replaced by one `<source>.unavailable` item, and its rows are kept
  (the item is unknown, not gone), so its recovery is not a new degradation.
  The unavailable item is blocked like any other and announced once, as the
  exit criterion asks (every item that becomes warning or blocked); the
  failure is also logged at ERROR for Nexotec (ADR-180).

Two runs for one Betrieb never both notify: each takes a transaction-scoped
advisory lock on the Betrieb first (Postgres; the SQLite fast lane runs one
at a time anyway). Raised by platform itself, so `notify` stays
platform-internal (no ADR-047 crossing); the contributions are read, never
written.
"""

import dataclasses
import datetime as dt
import hashlib
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.status import StatusItem, StatusState
from app.platform.administration.models.status_page import StatusItemState
from app.platform.administration.services import status_page
from app.platform.administration.services.setup_checklist_regressions import active_managers
from app.platform.identity.services import notifications
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.services.dealer_dealerships import dealer_dealerships_only

logger = logging.getLogger(__name__)

SUBJECT_TYPE = "status_item"
STATUS_PAGE_LINK = "/administration/status"


@dataclasses.dataclass(frozen=True)
class StatusNotificationRun:
    degraded: int
    cleared: int
    notified: int


def _lock_dealership(db: Session, dealership_id: uuid.UUID) -> None:
    """Serialises two runs for one Betrieb until the caller's transaction
    ends, so the second reads the first's rows and does not notify again."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"platform.status_page.notifications:{dealership_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def _deliver(
    db: Session, item: StatusItem, *, user_id: uuid.UUID, dealership_id: uuid.UUID, subject: notifications.NotificationSubject
) -> uuid.UUID | None:
    """The item's own notification when it names a catalogued one (KAN-776:
    Google Business Profile's «nothing to do on your side»), else the generic
    `administration.status_item_degraded`. Each call names its code as a
    literal (`test_notify_call_sites_use_registered_codes.py`)."""

    params = {"source": item.source, "state": item.state.value}
    if item.notification_event == NotificationEventCode.BUSINESS_PROFILE_INTERRUPTED.value:
        return notifications.notify(
            db,
            NotificationEventCode.BUSINESS_PROFILE_INTERRUPTED,
            user_id=user_id,
            dealership_id=dealership_id,
            subject=subject,
            params=params,
        )
    return notifications.notify(
        db,
        NotificationEventCode.STATUS_ITEM_DEGRADED,
        user_id=user_id,
        dealership_id=dealership_id,
        subject=subject,
        params=params,
    )


def _notify_managers(db: Session, dealership: Dealership, row: StatusItemState, item: StatusItem) -> int:
    notified = 0
    subject = notifications.NotificationSubject(type=SUBJECT_TYPE, id=row.id, link=STATUS_PAGE_LINK)
    for manager in active_managers(db, dealership.id):
        delivered = _deliver(db, item, user_id=manager.id, dealership_id=dealership.id, subject=subject)
        if delivered is not None:
            notified += 1
    return notified


def check_dealership(db: Session, dealership: Dealership, *, now: dt.datetime | None = None) -> StatusNotificationRun:
    """One Betrieb: compare, notify and record. Never commits."""

    _lock_dealership(db, dealership.id)
    now = now or utcnow()
    page = status_page.derive(db, dealership.id, now=now)
    rows = {
        row.item_key: row
        for row in db.scalars(select(StatusItemState).where(StatusItemState.dealership_id == dealership.id)).all()
    }
    degraded = cleared = notified = 0
    seen: set[str] = set()
    for item in page.items:
        seen.add(item.key)
        row = rows.get(item.key)
        if item.state == StatusState.OK:
            if row is not None and row.state != StatusState.OK:
                row.state = StatusState.OK
                row.changed_at = now
                row.notified_at = None
                cleared += 1
            continue
        was_ok = row is None or row.state == StatusState.OK
        escalated = row is not None and row.state == StatusState.WARNING and item.state == StatusState.BLOCKED
        if row is None:
            row = StatusItemState(
                dealership_id=dealership.id, item_key=item.key, source=item.source, state=item.state, changed_at=now
            )
            db.add(row)
        elif row.state != item.state:
            row.state = item.state
            row.changed_at = now
        if was_ok:
            row.notified_at = None
            degraded += 1
        elif not escalated or row.notified_at is not None:
            continue  # still degraded, and the managers were told
        exempt = item.notified_by_source and item.state == StatusState.WARNING
        if exempt:
            continue
        row.notified_at = now
        db.flush()
        notified += _notify_managers(db, dealership, row, item)
    for key, row in rows.items():
        if key in seen or row.source in page.unavailable_sources:
            continue
        if row.state != StatusState.OK:
            cleared += 1
        db.delete(row)
    db.flush()
    return StatusNotificationRun(degraded=degraded, cleared=cleared, notified=notified)


def run(db: Session) -> StatusNotificationRun:
    """Every active Betrieb, one transaction each; a failure on one is logged
    at ERROR and does not stop the others."""

    ids = db.scalars(
        select(Dealership.id)
        .where(Dealership.status == DealershipStatus.ACTIVE, dealer_dealerships_only())
        .order_by(Dealership.id)
    ).all()
    degraded = cleared = notified = 0
    now = utcnow()
    for dealership_id in ids:
        try:
            dealership = db.get(Dealership, dealership_id)
            if dealership is None:
                continue
            result = check_dealership(db, dealership, now=now)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "platform.status_page.notifications: business %s could not be checked",
                dealership_id,
                extra={"dealershipId": str(dealership_id)},
            )
            continue
        degraded += result.degraded
        cleared += result.cleared
        notified += result.notified
    if degraded or cleared:
        logger.info(
            "platform.status_page.notifications: %d degraded, %d cleared, %d notifications",
            degraded,
            cleared,
            notified,
        )
    return StatusNotificationRun(degraded=degraded, cleared=cleared, notified=notified)
