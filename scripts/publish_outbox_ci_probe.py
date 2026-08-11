"""CI-only companion to app.worker's DMS_OUTBOX_WORKER_CI_SMOKE_TEST_PROBE
gate: publishes one test.probe.ci_smoke_test event so the real worker
process has something to dispatch when the CI job runs it. Prints the
message id so the workflow can pass it to
scripts/verify_outbox_ci_probe.py without re-deriving it.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/publish_outbox_ci_probe.py
"""

import uuid

from app.core.outbox import OutboxEvent, publish
from app.db import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        message = publish(
            db,
            OutboxEvent(
                event_type="test.probe.ci_smoke_test",
                tenant_id=None,
                producer="test",
                aggregate_type="probe",
                aggregate_id=uuid.uuid4(),
                payload={"source": "ci"},
            ),
        )
        db.commit()
        print(message.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
