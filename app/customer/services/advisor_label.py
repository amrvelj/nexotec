"""KAN-403 — the nightly refresh of the customer's advisor label.

`Customer.advisor_label` is rule 2's copy of a platform user's `First Last`
beside `advisor_id`, written when the advisor is set. A user renamed later
(marriage, a typo fixed) would otherwise keep the old name on every customer
card for as long as the record lives. This job re-reads the names through
`app.platform.public` (no join into platform's tables) and commits per batch
in this context alone (ADR-047).
"""

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.customer.models.customer import Customer
from app.platform.public import get_user_display_labels

_BATCH = 500


def refresh_advisor_labels(db: Session) -> int:
    """Re-read every assigned advisor's name; returns how many customers'
    labels changed. Walks the customers in id order, one commit per batch.

    A changed label is written through the ORM (updated_at moves: what the API
    returns changed, so an `updatedSince` sync picks it up); `version` is not
    bumped — the label is a copy no client writes, as with the vehicle-party
    labels. A current label, and the label of a deactivated user (it keeps the
    name it had), only get `advisor_label_refreshed_at` stamped, with
    updated_at held. An advisor id that names no user is left untouched; the
    stale stamp is what the sync-age alarm then shows.

    Each batch is read FOR NO KEY UPDATE SKIP LOCKED: a customer another
    writer holds is left for the next night, never waited for. Waiting could
    deadlock — a merge D→T holds D and T and then locks the customers merged
    into D earlier (`_repoint_earlier_merges`, KAN-383), while the job, holding
    one of those, would wait for D. The job holds rows only while it rewrites
    them and waits on nothing, so no writer can be caught in a cycle with it.
    """

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(Customer)
            .where(Customer.advisor_id.is_not(None))
            .order_by(Customer.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(Customer.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        labels = get_user_display_labels(db, {c.advisor_id for c in batch if c.advisor_id is not None})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for customer in batch:
            current = labels.get(customer.advisor_id) if customer.advisor_id is not None else None
            if current is None:
                continue
            if current.deactivated or current.label == customer.advisor_label:
                stamp_only.append(customer.id)
                continue
            customer.advisor_label = current.label
            customer.advisor_label_refreshed_at = now
            changed += 1
        db.flush()
        if stamp_only:
            db.execute(
                update(Customer)
                .where(Customer.id.in_(stamp_only))
                .values(advisor_label_refreshed_at=now, updated_at=Customer.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def oldest_advisor_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest advisor label — what the worker heartbeat records as
    `dms.label.age_seconds{label="customer.advisor"}` (CLAUDE.md rule 10). The
    nightly job restamps every customer whose advisor it can resolve and whose
    row it can lock, so a value well past a day means the job has stopped, an
    advisor id names no user, or a customer was held by a writer when the job
    passed (that one clears itself the next night). None when no customer has
    an advisor."""

    oldest = db.scalar(
        select(func.min(Customer.advisor_label_refreshed_at)).where(
            Customer.advisor_id.is_not(None), Customer.advisor_label_refreshed_at.is_not(None)
        )
    )
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.UTC)
    return (utcnow() - oldest).total_seconds()
