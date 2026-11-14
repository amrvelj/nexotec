"""The daily jobs `app/worker.py` registers for platform. The first (KAN-200; Dealer
Administration FR-A-12): a user whose `valid_to` has passed is deactivated
automatically — "a leaver whose access depends on someone remembering is
the most common real-world security failure in a dealership". Nobody is
there to decide the reassignment step, so every open record goes to its
module's own unassigned queue (never silently with the leaver, FR-A-37);
the audit rows name the job as the reason.

The job acts with platform's own authority (a staff account past its
`valid_to` expires like any other), one user at a time: a user who is the
last active manager of their Betrieb, or of a sister Betrieb they manage
through a membership (`update_user`'s `last_manager_elsewhere`), is NOT
deactivated (FR-A-13 outranks the expiry), logged at ERROR — the alarm — and
tried again the next night; any
other failure on one user is logged the same way and does not stop the
others. A hand-over that fails after the status change is retried by the
next runs for `RETRY_WINDOW` (the user is already deactivated, so the due
list no longer carries them), and the nightly reconciliation of each module
reports an open record whose owner is deactivated for as long as it lasts
(rule 10: monitored, never assumed).

The second (KAN-559, `run_daily_revoked_grant_repair`) ends at the provider
every revoked connected-account grant still holding a reference.

KAN-218 adds one: `run_daily_data_export_expiry` deletes an export's file
seven days after it was produced. An offboarded Betrieb's archive is never
deleted by a job (Anto, 2026-11-14): reconciliation reports it once its ten
years have passed, and the deletion is a manual staff step.

KAN-214 adds `run_daily_setup_checklist_regressions`: a checklist item that
broke after activation raises one `administration` notification to the
Betrieb's managers per regression (`services/setup_checklist_regressions.py`).

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
from app.platform.identity.models.user import User, UserStatus
from app.platform.identity.schemas.user import UserUpdate
from app.platform.identity.services import connected_account, notifications, ownership, push_subscriptions
from app.platform.identity.services import user as user_service
from app.platform.identity.services import user_reads as user_reads_service
from app.platform.services import data_export, setup_checklist_regressions

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
    no_longer_due = 0
    for stale in due:
        user = stale
        try:
            # Re-read under the user-row lock (KAN-380's order) and judged
            # again: a PATCH that extended `validTo` or deactivated the user
            # after the list was read wins, and the version is bumped from the
            # row as it stands, never from the list's copy.
            user = user_reads_service.get_user_for_update_or_404(db, stale.tenant_id, stale.id)
            if not user_service.is_due_for_expiry(user, today=today):
                db.rollback()
                no_longer_due += 1
                continue
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
    # Each is re-read under the lock and must still be deactivated by this
    # job: a manager who reactivated them after the list was read keeps
    # their records. The lock is released before the modules' own sessions
    # run (ADR-047), as after the main loop's status change.
    just_done = {str(user.id) for user in due}
    retried = 0
    retry_reactivated = 0
    for listed in user_service.list_users_deactivated_for(db, reason=EXPIRY_REASON, since=utcnow() - RETRY_WINDOW):
        if str(listed.id) in just_done:
            continue
        try:
            user = user_reads_service.get_user_for_update_or_404(db, listed.tenant_id, listed.id)
        except Exception:
            db.rollback()
            handover_failed.append(str(listed.id))
            logger.exception("platform.user_expiry: user %s not re-read", listed.id, extra={"userId": str(listed.id)})
            continue
        still_expired = user.status == UserStatus.DEACTIVATED and user.deactivation_reason == EXPIRY_REASON
        db.rollback()
        if not still_expired:
            retry_reactivated += 1
            continue
        retried += 1
        if not _release(db, session_factory, user, late=True):
            handover_failed.append(str(user.id))

    summary = (
        f"due={len(due)} deactivated={deactivated} skipped_last_manager={len(skipped)} failed={len(failed)} "
        f"retried={retried} handover_failed={len(handover_failed)} no_longer_due={no_longer_due} "
        f"retry_reactivated={retry_reactivated}"
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


# --- staff notifications (KAN-216; Dealer Administration FR-A-48, FR-A-50) -------------------


def run_daily_notification_digest(db: Session) -> None:
    """`platform.notifications.digest`: every notification held back for a
    family its user set to `digest` reaches the inbox, and each such user
    gets one push pointing at the inbox (FR-A-48). One transaction: the
    release and its outbox rows commit together (rule 4)."""

    users = notifications.release_digests(db)
    db.commit()
    logger.info("platform.notifications.digest: %d user(s) got a digest", users, extra={"users": users})


def run_daily_notification_purge(db: Session) -> None:
    """`platform.notifications.purge`: the inbox keeps 90 days (FR-A-50);
    and no push subscription outlives its session (FR-A-49) — revoked ones
    went with the revocation (`auth_sessions.revoke*`); this takes the
    sessions that ran out, and any revoked one that slipped past."""

    purged = notifications.purge_expired(db)
    orphaned = push_subscriptions.delete_for_dead_sessions(db)
    db.commit()
    logger.info(
        "platform.notifications.purge: %d notification(s) past 90 days, %d subscription(s) of ended sessions",
        purged,
        orphaned,
        extra={"purged": purged, "orphanedSubscriptions": orphaned},
    )


def run_daily_revoked_grant_repair(db: Session) -> None:
    """KAN-559 (FR-AU-24; ADR-118): a leaver's mailbox grant must end at the
    provider. A revoked grant still holding a reference — its event
    dead-lettered during an outage longer than the outbox's retries, or
    consumed before its provider's revoker was registered — is ended now,
    by `connected_account.repair_revoked_grants`. Registered after
    `reconciliation.run_all` (ADR-047's addendum: finding and fixing stay
    apart), so the night's report names the grant before it is repaired."""

    run = connected_account.repair_revoked_grants(db)
    level = logging.ERROR if run.failed else logging.INFO
    logger.log(
        level,
        "platform.connected_account.repair: ended=%d no_revoker=%d failed=%d",
        run.ended,
        run.no_revoker,
        run.failed,
        extra={"ended": run.ended, "noRevoker": run.no_revoker, "failed": run.failed},
    )


def run_daily_data_export_expiry(db: Session) -> None:
    """KAN-218 (FR-A-44; Anto, 2026-11-13, Q1 A): an export's download stays
    seven days; then its file is deleted and the export reads `expired`, one
    audit row each. A new export can be requested at any time."""

    expired = data_export.expire_downloads(db)
    logger.info("platform.data_exports.expire: %d export file(s) deleted", expired, extra={"expired": expired})


def run_daily_setup_checklist_regressions(db: Session) -> None:
    """`platform.setup_checklist.regressions` (KAN-214; D-A-21, FR-A-26): every
    active Betrieb's checklist, one transaction each; an item broken since the
    last run notifies the managers once, an item complete again is closed."""

    setup_checklist_regressions.run(db)
