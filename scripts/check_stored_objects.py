"""KAN-402 (Anto, 2026-11-15, option A): the start-up check that every object
key a platform row holds names a stored object
(`app/platform/services/stored_objects_check.py`). render.yaml's
startCommand runs it on every start of the web service — after each deploy
and restart — before uvicorn, and never lets it hold the start up
(`|| true`): a finding is recorded in reconciliation_run /
reconciliation_orphan and logged at ERROR (Sentry, once configured), as the
nightly reconciliation's are.

    python scripts/check_stored_objects.py

Exit 0 when every object is there, 1 with the findings listed, 2 when the
check itself could not run (logged at ERROR too).
"""

import logging
import sys

from app.core.object_store import get_object_store
from app.core.observability import configure_logging, configure_sentry
from app.core.reconciliation import ReconciliationAlarm
from app.db import SessionLocal
from app.platform.services import stored_objects_check

logger = logging.getLogger("scripts.check_stored_objects")


def main() -> int:
    configure_logging()
    configure_sentry()
    db = SessionLocal()
    try:
        run = stored_objects_check.run(db, get_object_store())
    except ReconciliationAlarm as alarm:
        for orphan in alarm.orphans:
            logger.error("%s: %s %s", orphan.check_label, orphan.source_table, orphan.source_row_id)
        logger.error("stored-object check: %s", alarm)
        return 1
    except Exception:
        logger.exception("stored-object check could not run")
        return 2
    finally:
        db.close()
    logger.info("stored-object check: %d key columns checked, nothing missing (run %s)", run.checks_run, run.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
