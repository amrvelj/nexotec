"""Standard-text delegations (KAN-199, FR-A-28, D-A-23).

A manager grants a named user write access to one or more standard-text
categories without the manager flag. Full replacement of the set on every
write, audited with before/after and the granter; ended on deactivation.
The core gate `require_write_or_delegated("standard_texts")` asks the
checker this module registers whether the principal holds the category in
their active dealership — a delegation never travels into a sister Betrieb.
No standard-text endpoint exists yet to attach the gate to; the probe-route
test proves the hook.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.auth import Principal
from app.core.base import utcnow
from app.core.permissions import set_delegation_checker
from app.platform.models.standard_text_delegation import StandardTextCategory, StandardTextDelegation
from app.platform.models.user import User


def _rows(db: Session, user_id: uuid.UUID) -> list[StandardTextDelegation]:
    return list(
        db.scalars(
            select(StandardTextDelegation)
            .where(StandardTextDelegation.user_id == user_id)
            .order_by(StandardTextDelegation.category)
        ).all()
    )


def list_active_categories(db: Session, *, user_id: uuid.UUID) -> list[StandardTextCategory]:
    now = utcnow()
    return sorted((row.category for row in _rows(db, user_id) if row.is_active(now)), key=lambda c: c.value)


def set_delegations(
    db: Session, *, user: User, categories: set[StandardTextCategory], actor_id: uuid.UUID | None, reason: str | None = None
) -> tuple[list[StandardTextCategory], bool]:
    """Replace the set. Returns (active categories, whether anything
    changed). Flushes, never commits — the caller owns the transaction."""

    now = utcnow()
    wanted = {category.value for category in categories}
    rows = {row.category.value: row for row in _rows(db, user.id)}
    before = sorted(code for code, row in rows.items() if row.is_active(now))

    for code, row in rows.items():
        if code in wanted and not row.is_active(now):
            row.granted_at = now
            row.granted_by = actor_id
            row.valid_to = None
        elif code not in wanted and row.is_active(now):
            row.valid_to = now
    for code in wanted - set(rows):
        db.add(
            StandardTextDelegation(
                dealership_id=user.tenant_id,
                user_id=user.id,
                category=StandardTextCategory(code),
                granted_at=now,
                granted_by=actor_id,
                valid_to=None,
            )
        )
    after = sorted(wanted)
    changed = after != before
    if changed:
        db.flush()
        record_audit_event(
            db,
            entity_type="user",
            entity_id=user.id,
            tenant_id=user.tenant_id,
            action="text_delegation_set",
            actor_id=actor_id,
            before={"categories": before},
            after={"categories": after},
            reason=reason,
        )
    return [StandardTextCategory(code) for code in after], changed


def end_all(db: Session, *, user: User, actor_id: uuid.UUID | None, reason: str) -> None:
    """Deactivation ends every delegation (FR-A-28). Flushes, never commits —
    the caller's transaction (update_user) owns the commit."""

    set_delegations(db, user=user, categories=set(), actor_id=actor_id, reason=reason)


def holds_delegation(db: Session, principal: Principal, capability: str, category: str) -> bool:
    """The core gate's question (require_write_or_delegated): only the
    standard-texts capability is delegable, only in the dealership the
    delegation was granted in (the principal's active one), only while
    active."""

    if capability != "standard_texts":
        return False
    try:
        wanted = StandardTextCategory(category)
    except ValueError:
        return False
    now = utcnow()
    row = db.scalar(
        select(StandardTextDelegation).where(
            StandardTextDelegation.user_id == principal.user_id,
            StandardTextDelegation.dealership_id == principal.tenant_id,
            StandardTextDelegation.category == wanted,
        )
    )
    return row is not None and row.is_active(now)


set_delegation_checker(holds_delegation)
