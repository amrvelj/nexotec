"""CI-only companion to scripts/publish_outbox_ci_probe.py: confirms the
real `python -m app.worker` process (not poll_once() called from pytest)
actually claimed, dispatched and published the probe message, and that
its consumer's processed_event row exists.

Usage: DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... python scripts/verify_outbox_ci_probe.py <message-id>
"""

import sys
import uuid

from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.processed_event_model import ProcessedEvent
from app.db import SessionLocal


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: verify_outbox_ci_probe.py <message-id>")
        sys.exit(2)
    message_id = uuid.UUID(sys.argv[1])

    db = SessionLocal()
    try:
        message = db.get(OutboxMessage, message_id)
        if message is None:
            fail(f"outbox message {message_id} not found")
        if message.status != OutboxStatus.PUBLISHED:
            fail(f"outbox message {message_id} is {message.status.value}, not published (last_error={message.last_error!r})")
        if message.published_at is None:
            fail("published message has no published_at")

        processed = db.get(ProcessedEvent, (message_id, "ci.smoke_test_probe"))
        if processed is None:
            fail("no processed_event row for (message_id, ci.smoke_test_probe)")

        print(f"OK: {message_id} published and processed by the real worker entrypoint.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
