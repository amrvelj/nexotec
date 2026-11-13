"""The daily job `app/worker.py` registers for valuation (KAN-599):
`valuation.provider_quotes.purge_unused` deletes a provider quote no
valuation references, ten years after it was fetched
(app.valuation.services.provider_quote_retention, whose docstring has the
ruling and the residual race). A context-level module, like
app.vehicle.daily_jobs, so the worker never reaches into this context's
services.

One log line per run with its count in the message text (it reads the same
in any log format) and as `extra`. A run that raises rolls back the batch it
was in — every earlier batch is committed — and the scheduler retries the
job the same day (KAN-303); a repeat run finds the committed batches done.
"""

import logging

from sqlalchemy.orm import Session

from app.valuation.services.provider_quote_retention import purge_unused_quotes

logger = logging.getLogger(__name__)


def run_daily_unused_provider_quote_purge(db: Session) -> None:
    result = purge_unused_quotes(db)
    logger.info(
        "valuation.provider_quotes.purge_unused: purged=%d",
        result.purged,
        extra={"purged": result.purged},
    )
