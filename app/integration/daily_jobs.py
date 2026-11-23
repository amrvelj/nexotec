"""The composition root the daily-job scheduler calls (WP-6 PR-6).
Lives in `app.integration` because it is the piece that knows about
connections across every tenant, and PR-6's own retention/notification
tables live in this context too.

`run_daily_integration_jobs` is the single function `app/worker.py`
registers — expiry warnings, then retention purge, then the support
digest, all in one call so the digest (which needs "how many warnings
fired today") can use what the earlier step just produced without
persisting an intermediate table that would exist for no other reason.

Until 2026-10-29 this job also ran the daily catalogue delta for every
tenant with a vehicle-data connection and the sync-age alarm (A-12) on
top of it. Both are gone: no provider is ever asked for its whole
catalogue (ADR-023 as amended; KAN-485, `app/vehicle/services/
catalogue_sync.py`).
"""

import logging

from sqlalchemy.orm import Session

from app.integration.services import gbp_drift as gbp_drift_service
from app.integration.services import gbp_link as gbp_link_service
from app.integration.services import gbp_push as gbp_push_service
from app.integration.services import mail_link as mail_link_service
from app.integration.services import notifications as notification_service
from app.integration.services import retention as retention_service

logger = logging.getLogger("app.integration.daily_jobs")


def run_daily_retention_purge(db: Session) -> dict[str, int]:
    """ADR-024's three tiers, run unconditionally every day — a purge job
    that finds nothing to delete on most days is exactly what "healthy"
    looks like, never a reason to skip running it.
    """

    return {
        "callLogMetadata": retention_service.purge_call_log_metadata(db),
        "errorPayloads": retention_service.purge_error_payloads(db),
        "successPayloads": retention_service.purge_success_payloads(db),
    }


def run_daily_integration_jobs(db: Session) -> None:
    """The one function `app/worker.py` registers. Order matters: expiry
    warnings first (so the digest below has today's count), then the purge
    (retention doesn't depend on anything above it, but running it after
    the warnings means a failure there never leaves a half-purged table —
    the purge itself is the most mechanical step), then the digest.
    """

    try:
        warnings_sent = notification_service.check_and_send_expiry_warnings(db)
    except Exception:
        db.rollback()
        logger.exception("expiry-warning check failed")
        warnings_sent = []

    try:
        purge_counts = run_daily_retention_purge(db)
        if any(purge_counts.values()):
            logger.info("daily retention purge", extra=purge_counts)
    except Exception:
        db.rollback()
        logger.exception("daily retention purge failed")

    try:
        notification_service.send_daily_support_digest(db, warnings=warnings_sent)
    except Exception:
        db.rollback()
        logger.exception("daily support digest failed")


def run_daily_mail_link_state_purge(db: Session) -> None:
    """KAN-763 — deletes the mailbox link states (KAN-215) used or expired more than a day
    ago (`services/mail_link.py::purge_spent_link_states`). Registered in `app/worker.py` as
    its own job, so its run is recorded on its own."""

    deleted = mail_link_service.purge_spent_link_states(db)
    if deleted:
        logger.info("mail link-state purge", extra={"deleted": deleted})


def run_daily_business_profile_link_state_purge(db: Session) -> None:
    """KAN-226 — the same for the Google Business Profile link states
    (`services/gbp_link.py::purge_spent_link_states`), registered as its own job."""

    deleted = gbp_link_service.purge_spent_link_states(db)
    if deleted:
        logger.info("business profile link-state purge", extra={"deleted": deleted})


def run_daily_business_profile_horizon_push(db: Session) -> None:
    """KAN-793 — pushes every active Business Profile binding whose facts
    changed with the date (a closed day entered or left the one-year
    horizon) though no location event fired (`services/gbp_push.py::
    run_horizon_pushes`). Registered as its own job, just before the drift
    check, so the check compares what Google holds against the full horizon;
    a binding that failed makes the job raise and be retried the same day."""

    counts = gbp_push_service.run_horizon_pushes(db)
    if counts["pushed"]:
        logger.info("business profile horizon push", extra=counts)


def run_daily_business_profile_drift_check(db: Session) -> None:
    """KAN-226 PR-4 — reads every active Business Profile binding's Google
    location back and records, per binding, whether Google shows what
    Nexotec pushes (`services/gbp_drift.py`). Reports, never repairs. Registered
    as its own job, before the status page's notifications, so the night's
    drift reaches the managers the same night."""

    counts = gbp_drift_service.run_drift_checks(db)
    if any(counts.values()):
        logger.info("business profile drift check", extra=counts)
