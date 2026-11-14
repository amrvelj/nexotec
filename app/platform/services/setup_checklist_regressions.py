"""A checklist item that broke after activation (KAN-214; Dealer
Administration D-A-21, FR-A-26; D-A-40's `administration` family).

The daily job `platform.setup_checklist.regressions` derives the checklist of
every active Betrieb. An applicable item that is incomplete and has no open
`setup_checklist_regression` row gets one, and each active manager of the
Betrieb gets one `administration.checklist_item_regressed` notification —
one per regression, not one per night. An item complete again closes its
open row (`resolved_at`); breaking it again later is a new regression.

The action the item blocks is refused meanwhile by the module that runs it
(`platform.public.require_setup_item_for`), whether or not the job has run:
the job only tells the managers. Raised by platform itself, so `notify`
stays platform-internal (no ADR-047 crossing).
"""

import dataclasses
import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.setup_checklist import SetupChecklistRegression
from app.platform.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services import notifications, setup_checklist
from app.platform.services.notification_catalogue import NotificationEventCode
from app.platform.services.user_common import membership_is_active

logger = logging.getLogger(__name__)

SUBJECT_TYPE = "setup_checklist_item"


@dataclasses.dataclass(frozen=True)
class RegressionRun:
    opened: int
    resolved: int
    notified: int


def _managers(db: Session, dealership_id) -> list[User]:
    members = select(DealershipMembership.user_id).where(
        DealershipMembership.dealership_id == dealership_id,
        DealershipMembership.is_dealer_manager.is_(True),
        membership_is_active(),
    )
    return list(
        db.scalars(
            select(User)
            .where(
                User.status == UserStatus.ACTIVE,
                or_(
                    (User.tenant_id == dealership_id) & User.is_dealer_manager.is_(True),
                    User.id.in_(members),
                ),
            )
            .order_by(User.id)
        ).all()
    )


def check_dealership(db: Session, dealership: Dealership) -> RegressionRun:
    """One Betrieb: open, notify and resolve. Never commits."""

    checklist = setup_checklist.derive(db, dealership)
    open_rows = {
        row.item_code: row
        for row in db.scalars(
            select(SetupChecklistRegression).where(
                SetupChecklistRegression.dealership_id == dealership.id,
                SetupChecklistRegression.resolved_at.is_(None),
            )
        ).all()
    }
    opened = resolved = notified = 0
    now = utcnow()
    applicable = {item.code for item in checklist.items}
    for item in checklist.items:
        row = open_rows.get(item.code)
        if item.complete and row is not None:
            row.resolved_at = now
            resolved += 1
        elif not item.complete and row is None:
            regression = SetupChecklistRegression(dealership_id=dealership.id, item_code=item.code, detected_at=now)
            db.add(regression)
            db.flush()
            opened += 1
            for manager in _managers(db, dealership.id):
                delivered = notifications.notify(
                    db,
                    NotificationEventCode.CHECKLIST_ITEM_REGRESSED,
                    user_id=manager.id,
                    dealership_id=dealership.id,
                    subject=notifications.NotificationSubject(type=SUBJECT_TYPE, id=regression.id, link=item.link),
                    params={"item": item.code},
                )
                if delivered is not None:
                    notified += 1
    # An item that no longer applies (the module was withdrawn) is resolved.
    for code, row in open_rows.items():
        if code not in applicable and row.resolved_at is None:
            row.resolved_at = now
            resolved += 1
    db.flush()
    return RegressionRun(opened=opened, resolved=resolved, notified=notified)


def run(db: Session) -> RegressionRun:
    """Every active Betrieb, one transaction each; a failure on one is logged
    at ERROR and does not stop the others."""

    ids = db.scalars(
        select(Dealership.id).where(Dealership.status == DealershipStatus.ACTIVE).order_by(Dealership.id)
    ).all()
    opened = resolved = notified = 0
    for dealership_id in ids:
        try:
            dealership = db.get(Dealership, dealership_id)
            if dealership is None:
                continue
            result = check_dealership(db, dealership)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "platform.setup_checklist.regressions: Betrieb %s could not be checked",
                dealership_id,
                extra={"dealershipId": str(dealership_id)},
            )
            continue
        opened += result.opened
        resolved += result.resolved
        notified += result.notified
    if opened or resolved:
        logger.info(
            "platform.setup_checklist.regressions: %d opened, %d resolved, %d notifications",
            opened,
            resolved,
            notified,
        )
    return RegressionRun(opened=opened, resolved=resolved, notified=notified)
