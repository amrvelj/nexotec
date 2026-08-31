"""The composition root the daily-job scheduler calls (WP-6 PR-4; PR-6
adds retention/notification jobs alongside this one). Lives in
`app.integration` because it is the piece that knows about connections
across every tenant — `app.vehicle`'s own `catalogue_sync.py` only ever
knows about the one tenant it's currently syncing.

One tenant's provider outage never blocks another's: each tenant's own
sync+alarm step runs inside its own try/except, so one failure is logged
and skipped rather than aborting the whole daily job — `app.core.
daily_scheduler.run_due_daily_jobs` only retries the WHOLE registered
job on the next poll cycle if the job function itself raises, and a
single stuck tenant re-running every 1s for the rest of the day would be
worse than just letting the sync-age alarm (A-12) catch that tenant going
stale over the following days.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.integration.public import list_enabled_connection_tenant_ids_for_provider
from app.vehicle.public import NoVehicleDataConnectionError, check_sync_age_alarm_for_tenant, run_daily_delta_for_tenant

logger = logging.getLogger("app.integration.daily_jobs")

# These provider_codes are integration's own — the two rows this context
# itself seeds (PR-2/PR-3's migrations). app.vehicle's catalogue_sync.py
# independently knows the same two codes for its own connection lookup;
# the duplication is deliberate (not imported from app.vehicle.services,
# which the import-linter blocks from here anyway — only app.vehicle.
# public is reachable) rather than invented as a new shared constant
# neither context is naturally the owner of.
_VEHICLE_DATA_PROVIDER_CODES = ("auto_i_dat", "auto_i_dat_mock")


def _tenants_with_a_vehicle_data_connection(db: Session) -> set[uuid.UUID]:
    tenant_ids: set[uuid.UUID] = set()
    for provider_code in _VEHICLE_DATA_PROVIDER_CODES:
        tenant_ids.update(list_enabled_connection_tenant_ids_for_provider(db, provider_code=provider_code))
    return tenant_ids


def run_daily_catalogue_sync_and_alarm(db: Session) -> None:
    """Registered as `app/worker.py`'s first daily job. For every tenant
    with an enabled vehicle-data connection: run the daily delta (which
    refuses and falls back to a full reseed past the 3-month `ChangedSince`
    limit, per catalogue_sync.py's own rule), then check the sync-age
    alarm against the state that delta just refreshed. Today this only
    logs a stale-sync warning — PR-6 turns it into a real
    `IntegrationNotification` (T-30/14/7 style, one aggregated digest);
    the seam is deliberately already here so PR-6 has one call site to
    extend, not a new one to invent.
    """

    for tenant_id in _tenants_with_a_vehicle_data_connection(db):
        try:
            run_daily_delta_for_tenant(db, tenant_id=tenant_id)
        except NoVehicleDataConnectionError:
            # A connection could have been disabled between the
            # enumeration above and this call — not an error, just
            # nothing to do for this tenant today.
            db.rollback()
            continue
        except Exception:
            db.rollback()
            logger.exception("daily catalogue sync failed for tenant", extra={"tenantId": str(tenant_id)})
            continue

        try:
            if check_sync_age_alarm_for_tenant(db, tenant_id=tenant_id):
                logger.warning(
                    "sync-age alarm: provider System watermark is stale (A-12)", extra={"tenantId": str(tenant_id)}
                )
        except Exception:
            db.rollback()
            logger.exception("sync-age alarm check failed for tenant", extra={"tenantId": str(tenant_id)})
