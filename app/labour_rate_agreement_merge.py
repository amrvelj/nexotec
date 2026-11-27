"""Composition root for a labour-rate agreement's contact (KAN-831, ADR-232).

The agreement is platform's data and its contact is contact's; contact sits
above platform, so platform never reads it (ADR-131). This module, which may
import both `public`s, reads the contact and hands platform what it needs —
the pattern of `app/api/v1/labour_rate_agreements.py` (KAN-629). The worker
registers both functions (`app/worker.py`).

- `move_labour_rate_agreements_on_contact_merged`: the consumer
  `platform.labour_rate_agreements_on_contact_merged` of `contact.merged`.
  The merges are replayed in the order they happened (see the function); a
  survivor the group cannot read moves the rows without a new label.
- `refresh_agreement_contact_labels_daily`: the daily job
  `platform.labour_rate_agreement_labels`.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.public import (
    contact_display_name,
    get_contact_or_404,
    get_contact_sales_labels,
)
from app.core.errors import NotFoundError
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.processed_event_model import ProcessedEvent
from app.platform.public import (
    ContactNow,
    lock_agreement_contact,
    refresh_agreement_contact_labels,
    repoint_agreements_of_merged_contact,
)

logger = logging.getLogger(__name__)

CONSUMER_NAME = "platform.labour_rate_agreements_on_contact_merged"
DAILY_JOB_NAME = "platform.labour_rate_agreement_labels"


class EarlierMergeNotYetConsumed(Exception):
    """The duplicate was itself the survivor of an earlier merge whose event this
    consumer has not consumed yet: this message waits for it (the harness retries
    it with backoff, and dead-letters it — alerting — if the earlier one never
    comes through)."""


def _earlier_merge_into_pending(db: Session, *, contact_id: uuid.UUID, before: OutboxMessage) -> uuid.UUID | None:
    """The id of a `contact.merged` event into `contact_id`, created before
    `before`, that is still pending and that this consumer has not consumed —
    else None. An earlier merge published or dead-lettered before this
    consumer existed never blocks."""

    earlier = select(OutboxMessage.id).where(
        OutboxMessage.event_type == "contact.merged",
        OutboxMessage.payload["duplicateOfContactId"].as_string() == str(contact_id),
        OutboxMessage.status == OutboxStatus.PENDING,
        OutboxMessage.created_at <= before.created_at,
        OutboxMessage.id != before.id,
        ~select(ProcessedEvent.event_id)
        .where(ProcessedEvent.event_id == OutboxMessage.id, ProcessedEvent.consumer_name == CONSUMER_NAME)
        .exists(),
    )
    return db.scalar(earlier.limit(1))


def move_labour_rate_agreements_on_contact_merged(db: Session, message: OutboxMessage) -> None:
    """Moves the duplicate's agreements in platform's own transaction (ADR-047).
    Flushes only; the consumer harness commits with the processed-event row.

    **The merges are replayed in the order they happened** (ADR-232): option A
    judges the duplicate's rows against what the survivor held *when it was
    merged*, and contact's merge rewrites earlier «merged into» pointers
    (KAN-383), so no read of today's chain can rebuild that. So a merge D→T
    waits until every earlier merge into D (X→D) has been consumed here; then
    X→D always finds D unmoved, judges X's rows against D's and moves them onto
    D (merged, but its own merge is still to come), and D→T carries D's rows —
    X's among them — on to T. Any chain length, any delivery order."""

    if message.tenant_id is None:
        raise ValueError(f"contact.merged message {message.id} has no tenant_id.")
    payload = message.payload or {}
    duplicate_id = uuid.UUID(payload["contactId"])
    target_id = uuid.UUID(payload["duplicateOfContactId"])
    # Before any agreement is read: a create naming the duplicate in flight commits
    # first (and is moved below) or waits and reads it merged; the target's lock
    # makes a create for the target be seen below or wait.
    lock_agreement_contact(db, duplicate_id)
    lock_agreement_contact(db, target_id)
    pending = _earlier_merge_into_pending(db, contact_id=duplicate_id, before=message)
    if pending is not None:
        raise EarlierMergeNotYetConsumed(
            f"contact.merged {message.id}: the earlier merge {pending} into {duplicate_id} is not consumed yet."
        )
    try:
        label: str | None = contact_display_name(get_contact_or_404(db, message.tenant_id, target_id))
    except NotFoundError:
        label = None
    report = repoint_agreements_of_merged_contact(
        db, duplicate_id=duplicate_id, target_id=target_id, target_label=label
    )
    logger.info(
        "%s: %d agreement row(s) moved, %d set aside", CONSUMER_NAME, len(report.moved), len(report.set_aside)
    )


def _contacts_now(db: Session):
    def read(contact_ids: set[uuid.UUID]) -> dict[uuid.UUID, ContactNow]:
        return {
            contact_id: ContactNow(label=current.label, merged=current.merged)
            for contact_id, current in get_contact_sales_labels(db, contact_ids).items()
        }

    return read


def refresh_agreement_contact_labels_daily(db: Session) -> None:
    report = refresh_agreement_contact_labels(db, labels_of=_contacts_now(db))
    logger.info(
        "%s: %d agreement row(s) relabelled, %d left alone (no contact, or a merged one)",
        DAILY_JOB_NAME,
        len(report.changed),
        len(report.unresolved),
    )
