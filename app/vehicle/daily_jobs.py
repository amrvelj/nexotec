"""The daily job `app/worker.py` registers for vehicle's configurations
(KAN-270): `vehicle.configurations.purge_unattached` deletes a configuration
no host ever took, 30 days after it was created
(app.vehicle.services.configuration_retention, whose docstring has the
ruling, the safety net and the residual race). A context-level module, like
app.inventory.daily_jobs, so the worker never reaches into this context's
services.

**Vehicle names no host** (KAN-633, ADR-131): the hosts — Sales, Stock,
Valuation, and whichever context takes configurations next — sit above
vehicle in the layer order, so vehicle never imports them. Their read-only
answers to "which of these configurations do you hold?" are handed in by the
composition root that registers the job (`app/worker.py`,
`CONFIGURATION_HOSTS`); a new host is added there and in its own
reconciliation (the check that its rows name no configuration vehicle has
not stamped as taken), never here.

One log line per run with its counts in the message text (they read the same
in any log format) and as `extra`. A run that raises rolls back the batch it
was in — every earlier batch is committed — and the scheduler retries the
job the same day (KAN-303); a repeat run starts again from the oldest
unstamped configuration and finds the committed batches done.
"""

import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.vehicle.services.configuration_retention import HeldByHost, purge_unattached_configurations

logger = logging.getLogger(__name__)


def run_daily_unattached_configuration_purge(db: Session, *, held_by_hosts: Sequence[HeldByHost]) -> None:
    """`held_by_hosts` — every host context's `referenced_configuration_ids`,
    from the composition root. An empty sequence is refused: a purge that
    asks no host would delete every configuration a host holds but vehicle
    has not stamped yet (a fact not consumed)."""

    if not held_by_hosts:
        raise ValueError("The configuration purge needs every host's reader; none was given.")
    result = purge_unattached_configurations(db, held_by_hosts=held_by_hosts)
    counts = {"purged": result.purged, "stamped": result.stamped}
    logger.info(
        "vehicle.configurations.purge_unattached: purged=%d stamped=%d",
        result.purged,
        result.stamped,
        extra=counts,
    )
