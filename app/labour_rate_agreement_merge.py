"""Composition root for a labour-rate agreement's contact (KAN-831, ADR-232).

The agreement is platform's data and its contact is contact's; contact sits
above platform, so platform never reads it (ADR-131). This module, which may
import both `public`s, reads the contact and hands platform what it needs —
the pattern of `app/api/v1/labour_rate_agreements.py` (KAN-629). The worker
registers both functions (`app/worker.py`).

- `move_labour_rate_agreements_on_contact_merged`: the consumer
  `platform.labour_rate_agreements_on_contact_merged` of `contact.merged`.
  Delivery is unordered, so the survivor is re-read from its owner (the end of
  its merge chain), as Sales', Stock's and Aftersales' consumers do; a chain
  that leaves the group or does not end falls back to the payload's survivor,
  and a survivor the group cannot read moves the rows without a new label.
- `refresh_agreement_contact_labels_daily`: the daily job
  `platform.labour_rate_agreement_labels`.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.contact.public import (
    contact_display_name,
    get_contact_or_404,
    get_contact_sales_labels,
    resolve_live_contact_id,
)
from app.core.errors import NotFoundError
from app.core.outbox_model import OutboxMessage
from app.platform.public import (
    ContactNow,
    lock_agreement_contact,
    refresh_agreement_contact_labels,
    repoint_agreements_of_merged_contact,
)

logger = logging.getLogger(__name__)

CONSUMER_NAME = "platform.labour_rate_agreements_on_contact_merged"
DAILY_JOB_NAME = "platform.labour_rate_agreement_labels"


def move_labour_rate_agreements_on_contact_merged(db: Session, message: OutboxMessage) -> None:
    """Moves the duplicate's agreements in platform's own transaction (ADR-047).
    Flushes only; the consumer harness commits with the processed-event row."""

    if message.tenant_id is None:
        raise ValueError(f"contact.merged message {message.id} has no tenant_id.")
    payload = message.payload or {}
    duplicate_id = uuid.UUID(payload["contactId"])
    survivor_id = uuid.UUID(payload["duplicateOfContactId"])
    # Before any agreement is read: a create naming the duplicate in flight commits
    # first (and is moved below) or waits and reads it merged.
    lock_agreement_contact(db, duplicate_id)
    target_id = resolve_live_contact_id(db, group_id=message.tenant_id, contact_id=survivor_id) or survivor_id
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
