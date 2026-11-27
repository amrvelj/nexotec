"""A labour-rate agreement's contact: a merge moves it, a rename relabels it
(KAN-831, follow-up of KAN-806; ADR-232).

`labour_rate_agreement.contact_id` names a contact the contact context owns
(rule 2's three-column pattern: `contact_label`, `contact_label_refreshed_at`).
Contact sits above platform, so platform never reads it (ADR-131): the
composition roots (`app/labour_rate_agreement_merge.py`,
`app/api/v1/labour_rate_agreements.py`)
read the contact through `contact.public` and hand platform what it needs.

- **On a contact merge** (`repoint_agreements_of_merged_contact`, the
  consumer `platform.labour_rate_agreements_on_contact_merged`): the
  duplicate's agreement rows move to the event's survivor (the composition
  root replays the merges in the order they happened), with the survivor's name, in
  platform's own transaction (ADR-047). Moving the reference changes no rate:
  ruling 178 is about the amounts and dates.
- **The survivor's agreement survives, per Betrieb and category** (Anto,
  2026-11-25 and 2026-11-27, option A): once the survivor holds an agreement
  for a category in a Betrieb — a live, not set-aside rate row, whatever its
  date; an end row alone is no agreement — **every** row of the duplicate
  for that category there is set aside — none of them, whatever its date
  (a later rate, an end), ever changes the survivor's agreement. Where the
  survivor holds none, the duplicate's rows all move. Rows are written once
  (ruling 178), so a set-aside row is not deleted or withdrawn: it stays on
  the duplicate's id with `set_aside_at`, is never an answer of
  `labour_rate_for`, shows as superseded on the screen, and is not reported
  by reconciliation. If the Betrieb wants the duplicate's newer rate, it
  enters it again for the survivor (one new row).
- **Nightly** (`refresh_agreement_contact_labels`, the daily job
  `platform.labour_rate_agreement_labels`): a renamed contact reaches the
  agreements within a day — the shared rules of the label refreshes
  (`.claude/rules/events-worker.md`): no `version` bump, a current label only
  restamped, an id naming nothing or a merged contact left alone, batches
  `FOR NO KEY UPDATE SKIP LOCKED`.
- **A create racing a merge** is serialised by `lock_agreement_contact` (a
  transaction advisory lock on the contact's id), which the agreement POST
  takes before it reads the contact and the merge consumer before it reads
  the agreements: the POST commits first and its row is moved, or it waits
  and reads the contact merged (409).
"""

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.platform.organisation.models.labour_rate import LabourRateAgreement, LabourRateAgreementKind
from app.platform.organisation.services.labour_rates import agreement_audit

_BATCH = 500


@dataclass(frozen=True)
class ContactNow:
    """A contact as its owner names it now, read by the composition root."""

    label: str
    merged: bool


@dataclass
class MergeReport:
    moved: list[uuid.UUID] = field(default_factory=list)
    set_aside: list[uuid.UUID] = field(default_factory=list)


@dataclass
class RefreshReport:
    changed: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)
    restamped: int = 0
    # An id naming no contact, or a merged one: left alone.
    unresolved: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)


def lock_agreement_contact(db: Session, contact_id: uuid.UUID) -> None:
    """Serialises an agreement create naming `contact_id` with the merge
    consumer moving that contact's agreements (until the transaction ends)."""

    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"platform.labour_rate_agreement_contact:{contact_id}".encode()).digest()
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)})


def repoint_agreements_of_merged_contact(
    db: Session,
    *,
    duplicate_id: uuid.UUID,
    target_id: uuid.UUID,
    target_label: str | None,
) -> MergeReport:
    """Move the agreements naming the merged `duplicate_id` to `target_id`,
    or set aside every row of a category the survivor already holds in that
    Betrieb (option A). A contact id names one group's contact, so the rows
    are the group's (another group's would be reconciliation's finding). A
    `target_label` of None (a survivor the group cannot read) moves the id
    and keeps the label; the nightly job relabels it once it can. Each row
    moved or set aside is audited and gets a new version (an open withdraw
    dialog then refuses as stale). Idempotent: a redelivery finds nothing
    left on the duplicate but rows already set aside. Flushes only; the
    consumer's harness commits."""

    report = MergeReport()
    if duplicate_id == target_id:
        return report
    rows = list(
        db.scalars(
            select(LabourRateAgreement)
            .where(
                LabourRateAgreement.contact_id == duplicate_id,
                LabourRateAgreement.set_aside_at.is_(None),
            )
            .order_by(LabourRateAgreement.id)
            .with_for_update()
        )
    )
    if not rows:
        return report
    held = {
        (row.dealership_id, row.category_id)
        for row in db.scalars(
            select(LabourRateAgreement)
            .where(
                LabourRateAgreement.dealership_id.in_({row.dealership_id for row in rows}),
                LabourRateAgreement.contact_id == target_id,
                # An agreement is a rate: an end row alone (its rate withdrawn) holds none,
                # so the duplicate's rows of that category move (option A, second half).
                LabourRateAgreement.kind == LabourRateAgreementKind.RATE,
                LabourRateAgreement.withdrawn_at.is_(None),
                LabourRateAgreement.set_aside_at.is_(None),
            )
            .with_for_update()
        )
    }
    now = utcnow()
    for row in rows:
        before = agreement_audit(row)
        collides = (row.dealership_id, row.category_id) in held
        if collides:
            row.set_aside_at = now
            report.set_aside.append(row.id)
            action = "set_aside_by_contact_merge"
        else:
            row.contact_id = target_id
            if target_label is not None:
                row.contact_label = target_label[:200]
                row.contact_label_refreshed_at = now
            report.moved.append(row.id)
            action = "repoint_on_contact_merge"
        row.version += 1
        row.updated_by = None
        db.flush()
        record_audit_event(
            db,
            entity_type="labour_rate_agreement",
            entity_id=row.id,
            tenant_id=row.dealership_id,
            action=action,
            actor_id=None,
            before=before,
            after=agreement_audit(row),
            reason=f"contact {duplicate_id} merged into {target_id}",
        )
    return report


def refresh_agreement_contact_labels(
    db: Session,
    *,
    labels_of: Callable[[set[uuid.UUID]], Mapping[uuid.UUID, ContactNow]],
    commit: Callable[[], None] | None = None,
) -> RefreshReport:
    """Re-read the name of every agreement's contact through `labels_of`
    (the composition root's read of contact), in id order, one `commit()`
    per batch read FOR NO KEY UPDATE SKIP LOCKED: a row another writer
    holds is left for the next night."""

    finish = commit or db.commit
    report = RefreshReport()
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            select(LabourRateAgreement)
            .order_by(LabourRateAgreement.id)
            .limit(_BATCH)
            .with_for_update(key_share=True, skip_locked=True)
        )
        if last_id is not None:
            stmt = stmt.where(LabourRateAgreement.id > last_id)
        batch = list(db.scalars(stmt))
        if not batch:
            return report
        last_id = batch[-1].id
        labels = labels_of({row.contact_id for row in batch})
        now = utcnow()
        stamp_only: list[uuid.UUID] = []
        for row in batch:
            current = labels.get(row.contact_id)
            if current is None or current.merged:
                report.unresolved.append((row.id, row.contact_id))
                continue
            label = current.label[:200]
            if label == row.contact_label:
                stamp_only.append(row.id)
                continue
            report.changed.append((row.id, row.contact_label, label))
            row.contact_label = label
            row.contact_label_refreshed_at = now
        # A copy, refreshed on purpose: no version bump.
        db.flush()
        if stamp_only:
            db.execute(
                update(LabourRateAgreement)
                .where(LabourRateAgreement.id.in_(stamp_only))
                .values(contact_label_refreshed_at=now, updated_at=LabourRateAgreement.updated_at)
                .execution_options(synchronize_session=False)
            )
            report.restamped += len(stamp_only)
        finish()


def oldest_agreement_contact_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest contact label on an agreement that is not set
    aside — `dms.label.age_seconds{label="platform.labour_rate_agreement"}`.
    None when there is no such agreement."""

    stamp = db.scalar(
        select(func.min(LabourRateAgreement.contact_label_refreshed_at)).where(
            LabourRateAgreement.set_aside_at.is_(None)
        )
    )
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return (utcnow() - stamp).total_seconds()
