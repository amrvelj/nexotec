"""The daily job `app/worker.py` registers for platform (KAN-200; Dealer
Administration FR-A-12): a user whose `valid_to` has passed is deactivated
automatically — "a leaver whose access depends on someone remembering is
the most common real-world security failure in a dealership". Nobody is
there to decide the reassignment step, so every open record goes to its
module's own unassigned queue (never silently with the leaver, FR-A-37);
the audit rows name the job as the reason.

The job acts with platform's own authority (a staff account past its
`valid_to` expires like any other), one user at a time: a user who is their
Betrieb's last active manager is NOT deactivated (FR-A-13 outranks the
expiry), logged at ERROR — the alarm — and tried again the next night; any
other failure on one user is logged the same way and does not stop the
others. A hand-over that fails after the status change is retried by the
next runs for `RETRY_WINDOW` (the user is already deactivated, so the due
list no longer carries them), and the nightly reconciliation of each module
reports an open record whose owner is deactivated for as long as it lasts
(rule 10: monitored, never assumed).

Runs where `python -m app.worker` runs; staging runs no worker (ADR-081).
"""

import datetime as dt
import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.core.permissions import AccessRole
from app.db import SessionLocal
from app.platform.models.user import User, UserStatus
from app.platform.schemas.user import UserUpdate
from app.platform.services import ownership
from app.platform.services import user as user_service

logger = logging.getLogger(__name__)

EXPIRY_REASON = "valid_to_expired"
RETRY_WINDOW = dt.timedelta(days=30)
_SYSTEM_ROLES = frozenset({AccessRole.PLATFORM_ADMIN})


def _release(db: Session, session_factory: Callable[[], Session], user: User, *, late: bool) -> bool:
    """One user's hand-over to the queues; False when a module failed (logged)."""

    try:
        results, failed = ownership.release_everything(
            db, session_factory, user=user, actor_id=None, reason=EXPIRY_REASON
        )
    except Exception:
        db.rollback()
        logger.exception("platform.user_expiry: hand-over of user %s could not run", user.id, extra={"userId": str(user.id)})
        failed, results = None, []
    if failed is None or failed:
        logger.error(
            "platform.user_expiry: hand-over failed for user %s (%s); retried by the next runs for %d days, "
            "reported by reconciliation meanwhile",
            user.id,
            ", ".join(f"{f.module}/{f.group}: {f.reason} — {f.message}" for f in failed or []) or "not run",
            RETRY_WINDOW.days,
            extra={"userId": str(user.id)},
        )
        return False
    if late and any(result.count for result in results):
        logger.warning(
            "platform.user_expiry: late hand-over for user %s: %s",
            user.id,
            ", ".join(f"{r.module}/{r.group}={r.count}" for r in results if r.count),
            extra={"userId": str(user.id)},
        )
    return True


def run_daily_user_expiry(db: Session, *, session_factory: Callable[[], Session] = SessionLocal) -> None:
    today = dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()
    due = user_service.list_users_due_for_expiry(db, today=today)
    deactivated = 0
    skipped: list[str] = []
    failed: list[str] = []
    handover_failed: list[str] = []
    for user in due:
        try:
            user_service.update_user(
                db,
                user=user,
                data=UserUpdate(status=UserStatus.DEACTIVATED),
                actor_id=None,  # a system write (KAN-243: null actor)
                actor_roles=_SYSTEM_ROLES,
                reason=EXPIRY_REASON,
                allow_deactivation=True,
            )
        except BadRequestError as exc:  # the last-manager rule (FR-A-13)
            db.rollback()
            skipped.append(str(user.id))
            logger.error(
                "platform.user_expiry: user %s not deactivated: %s", user.id, exc, extra={"userId": str(user.id)}
            )
            continue
        except Exception:
            db.rollback()
            failed.append(str(user.id))
            logger.exception("platform.user_expiry: user %s not deactivated", user.id, extra={"userId": str(user.id)})
            continue
        deactivated += 1
        if not _release(db, session_factory, user, late=False):
            handover_failed.append(str(user.id))

    # The retry: users this job deactivated recently whose hand-over may have
    # failed — `release_everything` is a no-op for one with nothing open.
    just_done = {str(user.id) for user in due}
    retried = 0
    for user in user_service.list_users_deactivated_for(db, reason=EXPIRY_REASON, since=utcnow() - RETRY_WINDOW):
        if str(user.id) in just_done:
            continue
        retried += 1
        if not _release(db, session_factory, user, late=True):
            handover_failed.append(str(user.id))

    summary = (
        f"due={len(due)} deactivated={deactivated} skipped_last_manager={len(skipped)} failed={len(failed)} "
        f"retried={retried} handover_failed={len(handover_failed)}"
    )
    if skipped or failed or handover_failed:
        logger.error(
            "platform.user_expiry: %s skipped=%s failed=%s handover_failed=%s",
            summary,
            ",".join(skipped),
            ",".join(failed),
            ",".join(handover_failed),
        )
        return
    logger.info("platform.user_expiry: %s", summary, extra={"due": len(due), "deactivated": deactivated})
