"""The daily job `app/worker.py` registers for platform (KAN-200; Dealer
Administration FR-A-12): a user whose `valid_to` has passed is deactivated
automatically — "a leaver whose access depends on someone remembering is
the most common real-world security failure in a dealership". Nobody is
there to decide the reassignment step, so every open record goes to its
module's own unassigned queue (never silently with the leaver, FR-A-37);
the audit rows name the job as the reason.

A user who is their Betrieb's last active manager is NOT deactivated
(FR-A-13 outranks the expiry): the job logs it at ERROR — the alarm — and
tries again the next night, like the reservation sweep's failed release.
A failure in one user's hand-over is logged the same way and does not stop
the others; the next night retries the hand-over through the same step.

Runs where `python -m app.worker` runs; staging runs no worker (ADR-081).
"""

import datetime as dt
import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.db import SessionLocal
from app.platform.models.user import UserStatus
from app.platform.schemas.user import UserUpdate
from app.platform.services import ownership
from app.platform.services import user as user_service

logger = logging.getLogger(__name__)

EXPIRY_REASON = "valid_to_expired"


def run_daily_user_expiry(db: Session, *, session_factory: Callable[[], Session] = SessionLocal) -> None:
    today = dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()
    due = user_service.list_users_due_for_expiry(db, today=today)
    deactivated = 0
    skipped: list[str] = []
    failed: list[str] = []
    for user in due:
        try:
            user_service.update_user(
                db,
                user=user,
                data=UserUpdate(status=UserStatus.DEACTIVATED),
                actor_id=None,  # a system write (KAN-243: null actor)
                actor_roles=frozenset(),
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
        deactivated += 1
        try:
            ownership.release_everything(db, session_factory, user=user, actor_id=None, reason=EXPIRY_REASON)
        except Exception:
            failed.append(str(user.id))
            logger.exception(
                "platform.user_expiry: hand-over failed for user %s; retried tomorrow",
                user.id,
                extra={"userId": str(user.id)},
            )
    summary = (
        f"due={len(due)} deactivated={deactivated} skipped_last_manager={len(skipped)} handover_failed={len(failed)}"
    )
    if skipped or failed:
        logger.error("platform.user_expiry: %s skipped=%s failed=%s", summary, ",".join(skipped), ",".join(failed))
        return
    logger.info("platform.user_expiry: %s", summary, extra={"due": len(due), "deactivated": deactivated})
