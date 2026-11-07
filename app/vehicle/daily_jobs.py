"""The daily job `app/worker.py` registers for vehicle's configurations
(KAN-270): `vehicle.configurations.purge_unattached` deletes a configuration
no host ever took, 30 days after it was created
(app.vehicle.services.configuration_retention, whose docstring has the
ruling, the safety net and the residual race). A context-level module, like
app.inventory.daily_jobs, so the worker never reaches into this context's
services — and the one place vehicle names the host contexts' read-only
answers to "which of these configurations do you hold?".

One log line per run with its counts in the message text (they read the same
in any log format) and as `extra`. A run that raises rolls back the batch it
was in — every earlier batch is committed — and the scheduler retries the
job the same day (KAN-303); a repeat run starts again from the oldest
unstamped configuration and finds the committed batches done.
"""

import logging

from sqlalchemy.orm import Session

from app.inventory.public import referenced_configuration_ids as referenced_by_stock_items
from app.sales.public import referenced_configuration_ids as referenced_by_offers_and_contracts
from app.valuation.public import referenced_configuration_ids as referenced_by_valuations
from app.vehicle.services.configuration_retention import purge_unattached_configurations

logger = logging.getLogger(__name__)

# Every context whose rows store a configuration id (vehicle's own vehicle
# records are the configuration's `vehicle_id`, read in the service). A new
# host adds its reader here and its `configuration_attached` fact.
_HOSTS = (
    referenced_by_offers_and_contracts,
    referenced_by_stock_items,
    referenced_by_valuations,
)


def run_daily_unattached_configuration_purge(db: Session) -> None:
    result = purge_unattached_configurations(db, held_by_hosts=_HOSTS)
    counts = {"purged": result.purged, "stamped": result.stamped}
    logger.info(
        "vehicle.configurations.purge_unattached: purged=%d stamped=%d",
        result.purged,
        result.stamped,
        extra=counts,
    )
