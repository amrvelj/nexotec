"""The status page's notifications (KAN-223; D-A-38, D-A-40's
`administration` family).

The daily job `platform.status_page.notifications` derives the page of every
active Betrieb and compares each item with its `status_item_state` row:

- an item that is warning or blocked and was ok (or never seen) raises
  exactly one `administration.status_item_degraded` notification to each
  active manager of the Betrieb, and the row records it;
- while it stays degraded — warning to blocked included — nothing more is
  raised: the dedup lasts until the item clears;
- an item ok again sets its row to ok, so a later degradation is a new one;
- an item that is gone (a connection deleted, an incident over) drops its row.

An item its source already notifies about (`notified_by_source`: an
integration nearing expiry, KAN-222's T-30/14/7 notices) is recorded but not
notified a second time. Raised by platform itself, so `notify` stays
platform-internal (no ADR-047 crossing); the contributions are read, never
written.
"""

import dataclasses
import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.status import StatusState
from app.platform.identity.services import notifications
from app.platform.identity.services.notification_catalogue import NotificationEventCode
from app.platform.models.status_page import StatusItemState
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import status_page
from app.platform.services.setup_checklist_regressions import active_managers

logger = logging.getLogger(__name__)

SUBJECT_TYPE = "status_item"
STATUS_PAGE_LINK = "/administration/status"


@dataclasses.dataclass(frozen=True)
class StatusNotificationRun:
    degraded: int
    cleared: int
    notified: int


def check_dealership(db: Session, dealership: Dealership, *, now: dt.datetime | None = None) -> StatusNotificationRun:
    """One Betrieb: compare, notify and record. Never commits."""

    now = now or utcnow()
    page = status_page.derive(db, dealership.id, now=now)
    rows = {
        row.item_key: row
        for row in db.scalars(select(StatusItemState).where(StatusItemState.dealership_id == dealership.id)).all()
    }
    degraded = cleared = notified = 0
    managers = None
    seen: set[str] = set()
    for item in page.items:
        seen.add(item.key)
        row = rows.get(item.key)
        was_ok = row is None or row.state == StatusState.OK
        if item.state == StatusState.OK:
            if row is not None and row.state != StatusState.OK:
                row.state = StatusState.OK
                row.changed_at = now
                cleared += 1
            continue
        if row is None:
            row = StatusItemState(dealership_id=dealership.id, item_key=item.key, state=item.state, changed_at=now)
            db.add(row)
        elif row.state != item.state:
            row.state = item.state
            row.changed_at = now
        if not was_ok:
            continue
        degraded += 1
        if item.notified_by_source:
            continue
        row.notified_at = now
        db.flush()
        if managers is None:
            managers = active_managers(db, dealership.id)
        for manager in managers:
            delivered = notifications.notify(
                db,
                NotificationEventCode.STATUS_ITEM_DEGRADED,
                user_id=manager.id,
                dealership_id=dealership.id,
                subject=notifications.NotificationSubject(type=SUBJECT_TYPE, id=row.id, link=STATUS_PAGE_LINK),
                params={"source": item.source, "state": item.state.value},
            )
            if delivered is not None:
                notified += 1
    for key, row in rows.items():
        if key not in seen:
            if row.state != StatusState.OK:
                cleared += 1
            db.delete(row)
    db.flush()
    return StatusNotificationRun(degraded=degraded, cleared=cleared, notified=notified)


def run(db: Session) -> StatusNotificationRun:
    """Every active Betrieb, one transaction each; a failure on one is logged
    at ERROR and does not stop the others."""

    ids = db.scalars(
        select(Dealership.id).where(Dealership.status == DealershipStatus.ACTIVE).order_by(Dealership.id)
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
                "platform.status_page.notifications: Betrieb %s could not be checked",
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
