"""A customer's contact groups (KAN-466; Anto, 2026-10-26).

The groups are the platform reference list `contact_group`; which of them a
Betrieb uses is its own switch in Dealer Administration. This module asks
platform which codes the **acting** Betrieb may set
(`platform.public.contact_group_codes`) and judges a change against it:

- a code the acting Betrieb may set can be added and removed;
- any other code the customer already carries — a group switched off since,
  one platform staff deactivated, or one a sister Betrieb of the group set —
  is **read-only** from here: it stays on the customer, and a write that would
  drop it is refused (422 `contact_group_read_only`), never silently kept;
- a code the acting Betrieb may not set cannot be added (422
  `contact_group_not_enabled`, or `contact_group_unknown` for a code the
  platform list does not have).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.customer.models.customer import Customer, CustomerContactGroup
from app.platform.public import contact_group_codes

AUDIT_FIELD = "contact_groups"


def customer_contact_groups(db: Session, customer_id: uuid.UUID) -> list[str]:
    return sorted(
        db.scalars(select(CustomerContactGroup.value_code).where(CustomerContactGroup.customer_id == customer_id)).all()
    )


def contact_groups_by_customer(db: Session, customer_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = {}
    ids = list(customer_ids)
    if not ids:
        return out
    for row in db.scalars(select(CustomerContactGroup).where(CustomerContactGroup.customer_id.in_(ids))).all():
        out.setdefault(row.customer_id, []).append(row.value_code)
    return {customer_id: sorted(codes) for customer_id, codes in out.items()}


def check_contact_group_change(db: Session, *, dealership_id: uuid.UUID, current: set[str], wanted: set[str]) -> None:
    """Refuse a change the acting Betrieb may not make (module docstring)."""

    added = wanted - current
    removed = current - wanted
    if not added and not removed:
        return
    codes = contact_group_codes(db, dealership_id)
    unknown = sorted(added - codes.known)
    if unknown:
        raise UnprocessableEntityError(
            f"Unknown contact group: {', '.join(unknown)}.",
            details={"reason": "contact_group_unknown", "valueCodes": unknown},
        )
    not_enabled = sorted(added - codes.settable)
    if not_enabled:
        raise UnprocessableEntityError(
            f"Contact group not switched on for this Betrieb: {', '.join(not_enabled)}.",
            details={"reason": "contact_group_not_enabled", "valueCodes": not_enabled},
        )
    read_only = sorted(removed - codes.settable)
    if read_only:
        raise UnprocessableEntityError(
            f"Contact group cannot be removed from this Betrieb: {', '.join(read_only)}.",
            details={"reason": "contact_group_read_only", "valueCodes": read_only},
        )


def replace_customer_contact_groups(
    db: Session, customer: Customer, wanted: Iterable[str], *, actor_id: uuid.UUID
) -> None:
    """Reconcile the rows to exactly `wanted` (already checked): insert the
    missing, delete the absent. The caller commits."""

    existing = {
        row.value_code: row
        for row in db.scalars(select(CustomerContactGroup).where(CustomerContactGroup.customer_id == customer.id)).all()
    }
    wanted_set = set(wanted)
    for code in sorted(wanted_set - existing.keys()):
        db.add(
            CustomerContactGroup(
                group_id=customer.group_id,
                customer_id=customer.id,
                value_code=code,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    for code, row in existing.items():
        if code not in wanted_set:
            db.delete(row)


def repoint_contact_groups(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    """A merge carries the duplicate's groups onto the survivor; a group the
    survivor already carries is dropped from the duplicate rather than
    colliding on the unique constraint. Every code moves, whatever the acting
    Betrieb has switched on: the merge loses nothing."""

    target_codes = set(
        db.scalars(select(CustomerContactGroup.value_code).where(CustomerContactGroup.customer_id == target_id)).all()
    )
    repointed = dropped = 0
    for row in db.scalars(select(CustomerContactGroup).where(CustomerContactGroup.customer_id == duplicate_id)).all():
        if row.value_code in target_codes:
            db.delete(row)
            dropped += 1
        else:
            row.customer_id = target_id
            target_codes.add(row.value_code)
            repointed += 1
    return repointed, dropped
