"""LegalBasis service layer (WP-3 PR-4, ADR-030): recording revDSG
joint-controllership evidence, and the one compliance predicate the
group-read helper is built around. See app.contact.models.legal_basis for
why every write here is an INSERT, never an UPDATE.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.models.contact import Contact
from app.contact.models.legal_basis import LegalBasis
from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.core.tenancy import get_group_read_or_404


def record_legal_basis(
    db: Session,
    *,
    contact_id: uuid.UUID,
    group_id: uuid.UUID,
    basis: str,
    scope: str,
    source_document: str,
    actor_id: uuid.UUID,
) -> LegalBasis:
    row = LegalBasis(
        contact_id=contact_id,
        group_id=group_id,
        basis=basis,
        scope=scope,
        source_document=source_document,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def withdraw_legal_basis(
    db: Session, *, contact_id: uuid.UUID, group_id: uuid.UUID, actor_id: uuid.UUID
) -> LegalBasis:
    """A withdrawal is a NEW row with withdrawn_at set, not a mutation of
    the grant — the grant row it withdraws stays exactly as it was
    recorded, an honest history rather than an edited one.
    """

    live = _live_basis(db, contact_id=contact_id, group_id=group_id)
    if live is None:
        raise NotFoundError(f"No live legal basis for contact {contact_id} in group {group_id} to withdraw.")

    row = LegalBasis(
        contact_id=contact_id,
        group_id=group_id,
        basis=live.basis,
        scope=live.scope,
        source_document=live.source_document,
        withdrawn_at=utcnow(),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _live_basis(db: Session, *, contact_id: uuid.UUID, group_id: uuid.UUID) -> LegalBasis | None:
    """Most recent row for this (contact, group) pair, if it isn't itself
    a withdrawal — the row-per-event history means "live" is a query, not
    a column.
    """

    latest = db.scalar(
        select(LegalBasis)
        .where(LegalBasis.contact_id == contact_id, LegalBasis.group_id == group_id)
        .order_by(LegalBasis.granted_at.desc(), LegalBasis.id.desc())
        .limit(1)
    )
    if latest is None or latest.withdrawn_at is not None:
        return None
    return latest


def has_live_basis(db: Session, *, contact_id: uuid.UUID, group_id: uuid.UUID) -> bool:
    return _live_basis(db, contact_id=contact_id, group_id=group_id) is not None


def has_any_basis_for_group(db: Session, *, group_id: uuid.UUID) -> bool:
    """The platform_admin flag-flip precondition (ADR-030 (2)) — at least
    one basis has EVER been recorded for this group, live or withdrawn. Not
    the same check as has_live_basis: a group can legitimately flip the
    flag on with one contact's basis recorded and others still pending —
    this only guards against flipping it on with zero paperwork at all.
    """

    return (
        db.scalar(select(LegalBasis.id).where(LegalBasis.group_id == group_id).limit(1)) is not None
    )


def get_contact_group_read_or_404(
    db: Session, *, group_read_enabled: bool, contact_id: uuid.UUID, group_id: uuid.UUID
) -> Contact:
    """THE sanctioned path for reading a contact by group_id under the
    ADR-030 compliance gate — app.core.tenancy.get_group_read_or_404 is the
    one enumerated function that can do this (see the lint rule in
    tests/architecture/test_no_ambient_group_read.py); this wraps it with
    the actual contact-context predicate app.core can't know about.
    Ready for its first real caller (a future group-wide reporting/Stock
    view) — no such consumer exists in this codebase yet, so this is proven
    directly against the service layer rather than through an invented
    endpoint (Build Sequence's own rule: an invented surface is the most
    expensive mistake available).
    """

    return get_group_read_or_404(
        db,
        Contact,
        contact_id,
        group_id,
        is_authorized=lambda: group_read_enabled
        and has_live_basis(db, contact_id=contact_id, group_id=group_id),
    )
