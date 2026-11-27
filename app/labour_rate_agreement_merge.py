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

from sqlalchemy import literal, select, tuple_
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
    """An earlier merge into the duplicate or into the survivor has not been
    consumed here yet: this message waits for it (the harness retries it with
    backoff, and dead-letters it — alerting — if the earlier one never comes
    through; replay dead letters oldest first)."""


def _earlier_merge_pending(
    db: Session, *, contact_ids: tuple[uuid.UUID, ...], before: OutboxMessage
) -> uuid.UUID | None:
    """The id of a `contact.merged` event into one of `contact_ids`, created
    before `before` (by `created_at`, a tie broken by `id`), that this consumer has not consumed and that is still
    pending or was dead-lettered — else None. A dead one blocks too: replaying
    it after a later merge went ahead would leave its rows on a contact whose
    own merge is already consumed, so the later merge waits (and dead-letters,
    named by the alert) and the operator replays the two oldest first. An
    earlier merge already published — before this consumer existed, or by it —
    never blocks."""

    earlier = select(OutboxMessage.id).where(
        OutboxMessage.event_type == "contact.merged",
        OutboxMessage.payload["duplicateOfContactId"].as_string().in_([str(c) for c in contact_ids]),
        OutboxMessage.status.in_([OutboxStatus.PENDING, OutboxStatus.DEAD]),
        # (created_at, id): two merges sharing a created_at are ordered by id —
        # the worker's own order (UUIDv7) — so neither waits for the other (KAN-857).
        tuple_(OutboxMessage.created_at, OutboxMessage.id)
        < tuple_(
            literal(before.created_at, OutboxMessage.created_at.type), literal(before.id, OutboxMessage.id.type)
        ),
        ~select(ProcessedEvent.event_id)
        .where(ProcessedEvent.event_id == OutboxMessage.id, ProcessedEvent.consumer_name == CONSUMER_NAME)
        .exists(),
    )
    return db.scalar(earlier.order_by(OutboxMessage.created_at, OutboxMessage.id).limit(1))


def move_labour_rate_agreements_on_contact_merged(db: Session, message: OutboxMessage) -> None:
    """Moves the duplicate's agreements in platform's own transaction (ADR-047).
    Flushes only; the consumer harness commits with the processed-event row.

    **The merges are replayed in the order they happened** (ADR-232): option A
    judges the duplicate's rows against what the survivor held *when it was
    merged*, and contact's merge rewrites earlier «merged into» pointers
    (KAN-383), so no read of today's chain can rebuild that. So a merge D→T
    waits until every earlier merge into D (X→D, a chain) and into T (Y→T,
    fan-in) has been consumed here; then X→D always finds D unmoved, judges
    X's rows against D's and moves them onto D (merged, but its own merge is
    still to come), D→T carries D's rows — X's among them — on to T, and T is
    judged with Y's rows already there. Any chain, any fan-in, any delivery
    order — of merges committed before the later one is consumed: two merges
    into one survivor take only a shared lock on it (contact's merge), so they
    can commit at the same instant, and one created later but visible first is
    consumed before the earlier one exists for this read; the pair then follows
    option A in consumption order, which is defensible for truly simultaneous
    merges (KAN-857). In normal operation the wait never fires: the worker dispatches by
    id (UUIDv7, the merges' own order) and each consumer commits before the
    next message; it fires only while this consumer fails on an earlier merge.
    Reading the outbox to order itself ties this consumer to the Postgres
    transport (ADR-006 keeps a broker as the destination) — recorded in
    ADR-232."""

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
    pending = _earlier_merge_pending(db, contact_ids=(duplicate_id, target_id), before=message)
    if pending is not None:
        raise EarlierMergeNotYetConsumed(
            f"contact.merged {message.id}: the earlier merge {pending} into {duplicate_id} or {target_id}"
            " is not consumed yet."
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
