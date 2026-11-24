"""The roles a contact holds (KAN-464 PR-1; Anto, 2026-10-26: one register,
roles — Kunde, Lieferant).

A role is a `contact_role` row: open while held, closed (`valid_to`) when
taken away, never deleted. A contact is never duplicated because it gains a
role (ADR-014).

**What belongs to the customer role** (Q1, Anto, 2026-11-14, A): the
lifecycle status (prospect / active / inactive), the credit block and the
advisor. Contact points, consent, do-not-contact, tags, contact groups and
the legal basis belong to every contact. So a contact without the customer
role (a supplier only):

- has no lifecycle status of its own. Its stored value is the neutral
  `active` — or `do_not_contact` / `merged`, which belong to every contact.
  The list's lifecycle filters on prospect / active / inactive match only
  customers; «ist nicht» keeps a supplier, which has no such value
  (ADR-102). Setting prospect / active / inactive on it is 422
  `customer_role_required`;
- has no advisor: none is defaulted on create (D-24 is a customer rule),
  and setting one is 422 `customer_role_required`. It never enters the
  unassigned queue (FR-A-37);
- cannot be credit-blocked (422 `customer_role_required`).

Taking the customer role away closes those fields in the same audited
write: prospect / inactive become `active`, the advisor is cleared. A
credit block is a deliberate fact someone must lift first: taking the role
away from a blocked contact is 409 `customer_role_credit_blocked`.

**Who may give which role** (Q3, Anto, 2026-11-14, A): sales and
aftersales give both roles; inventory (purchasing) writes contacts too but
gives only the supplier role. The matrix row `contact_customer_role` says
who may write the customer role's data — giving or taking the role, the
lifecycle values that are the role's, the advisor, the credit block, and a
merge of a contact that holds it (the merge moves the role and its
fields). Anyone the `contacts` row lets write does the rest: contact
points, consent, setting do-not-contact (lifting it on a customer is the
customer role's), tags, contact groups, the supplier role.
Outside it a write is 403 `customer_role_not_permitted` and changes
nothing. The API passes the principal's cell as `may_write_customer_role`;
the service judges it against the roles read under the same locks as the
rules above.
"""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.contact.models.contact import Contact, ContactLifecycleStatus, ContactRole, ContactRoleAssignment
from app.core.base import utcnow
from app.core.errors import ConflictError, ForbiddenError, UnprocessableEntityError

AUDIT_FIELD = "roles"
OPEN_ROLE_CONSTRAINT = "uq_contact_role_contact_id_role_open"

# The lifecycle values that are the customer role's (Q1); `do_not_contact`
# and `merged` belong to every contact.
CUSTOMER_LIFECYCLE_STATUSES = frozenset(
    {ContactLifecycleStatus.PROSPECT, ContactLifecycleStatus.ACTIVE, ContactLifecycleStatus.INACTIVE}
)
# The PATCH keys judged against the roles (and so taken under the row lock).
CUSTOMER_ROLE_FIELDS = frozenset({"roles", "lifecycle_status", "advisor_id"})
# What a contact without the customer role stores as its lifecycle status.
NEUTRAL_LIFECYCLE_STATUS = ContactLifecycleStatus.ACTIVE


def customer_role_required(field: str) -> UnprocessableEntityError:
    return UnprocessableEntityError(
        f"'{field}' belongs to the customer role, which this contact does not hold.",
        details={"reason": "customer_role_required", "field": field},
    )


CUSTOMER_ROLE_CAPABILITY = "contact_customer_role"


def customer_role_not_permitted(field: str) -> ForbiddenError:
    return ForbiddenError(
        f"'{field}' belongs to the customer role, which this user may not write.",
        details={"reason": "customer_role_not_permitted", "field": field},
    )


def check_customer_role_write_on_update(
    contact: Contact, *, may_write_customer_role: bool, held_customer_before: bool, holds_customer_after: bool,
    changes: dict[str, Any],
) -> None:
    """Q3: a PATCH by someone who may not write the customer role's data
    must leave it alone — the role itself, and on a contact holding it a
    changed lifecycle value of the role's or a changed advisor. `changes`
    is the PATCH's `model_dump(exclude_unset=True)` before the roles are
    popped; a field sent unchanged is no write."""

    if may_write_customer_role:
        return
    if held_customer_before != holds_customer_after:
        raise customer_role_not_permitted("roles")
    if not held_customer_before:
        return
    # Do-not-contact is every contact's, so every writer sets it. Lifting it
    # on a customer chooses a value of the customer role's (prospect /
    # active / inactive) — else setting and lifting it would turn a prospect
    # into an active customer in two steps. On a supplier-only contact
    # (returned above) every writer lifts it.
    status = changes.get("lifecycle_status")
    if status in CUSTOMER_LIFECYCLE_STATUSES and status != contact.lifecycle_status:
        raise customer_role_not_permitted("lifecycleStatus")
    if "advisor_id" in changes and changes["advisor_id"] != contact.advisor_id:
        raise customer_role_not_permitted("advisorId")


def open_roles(db: Session, contact_id: uuid.UUID) -> list[str]:
    return sorted(
        role.value
        for role in db.scalars(
            select(ContactRoleAssignment.role).where(ContactRoleAssignment.contact_id == contact_id, ContactRoleAssignment.valid_to.is_(None))
        ).all()
    )


def holds_customer_role_any(db: Session, contact_ids: Iterable[uuid.UUID]) -> bool:
    return (
        db.scalar(
            select(ContactRoleAssignment.id).where(
                ContactRoleAssignment.contact_id.in_(list(contact_ids)),
                ContactRoleAssignment.role == ContactRole.CUSTOMER,
                ContactRoleAssignment.valid_to.is_(None),
            ).limit(1)
        )
        is not None
    )


def roles_by_contact(db: Session, contact_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[ContactRole]]:
    ids = list(contact_ids)
    out: dict[uuid.UUID, list[ContactRole]] = {}
    if not ids:
        return out
    for row in db.scalars(
        select(ContactRoleAssignment).where(ContactRoleAssignment.contact_id.in_(ids), ContactRoleAssignment.valid_to.is_(None))
    ).all():
        out.setdefault(row.contact_id, []).append(row.role)
    return {contact_id: sorted(roles, key=lambda role: role.value) for contact_id, roles in out.items()}


def holds_role(role: ContactRole) -> ColumnElement[bool]:
    """The list predicate: the contact holds `role` today. It narrows a
    statement already scoped to the caller's group — by the contact's id,
    so it reads no other group's row and filters on no group itself."""

    return Contact.id.in_(
        select(ContactRoleAssignment.contact_id).where(ContactRoleAssignment.role == role, ContactRoleAssignment.valid_to.is_(None))
    )


def check_create(roles: list[ContactRole], *, fields_set: set[str], lifecycle_status: ContactLifecycleStatus,
                 advisor_id: uuid.UUID | None) -> ContactLifecycleStatus:
    """The lifecycle status a new contact is stored with; refuses a customer
    field on a contact created without the customer role."""

    if ContactRole.CUSTOMER in roles:
        return lifecycle_status
    if "lifecycle_status" in fields_set and lifecycle_status in CUSTOMER_LIFECYCLE_STATUSES - {NEUTRAL_LIFECYCLE_STATUS}:
        raise customer_role_required("lifecycleStatus")
    if advisor_id is not None:
        raise customer_role_required("advisorId")
    return lifecycle_status if lifecycle_status not in CUSTOMER_LIFECYCLE_STATUSES else NEUTRAL_LIFECYCLE_STATUS


def apply_role_rules_on_update(contact: Contact, *, holds_customer_after: bool, held_customer_before: bool,
                               changes: dict[str, Any]) -> None:
    """Judges a PATCH against the roles it leaves the contact with, and puts
    the closing of the customer fields into `changes` when the PATCH takes
    the customer role away (module docstring). `changes` is the PATCH's
    `model_dump(exclude_unset=True)`, roles already popped."""

    if holds_customer_after:
        return
    if changes.get("lifecycle_status") in CUSTOMER_LIFECYCLE_STATUSES - {NEUTRAL_LIFECYCLE_STATUS}:
        raise customer_role_required("lifecycleStatus")
    if changes.get("advisor_id") is not None:
        raise customer_role_required("advisorId")
    if not held_customer_before:
        return
    if contact.credit_block:
        raise ConflictError(
            "The customer role cannot be taken away while the contact is credit-blocked — lift the block first.",
            details={"reason": "customer_role_credit_blocked"},
        )
    if "lifecycle_status" not in changes and contact.lifecycle_status in CUSTOMER_LIFECYCLE_STATUSES:
        changes["lifecycle_status"] = NEUTRAL_LIFECYCLE_STATUS
    if "advisor_id" not in changes and contact.advisor_id is not None:
        changes["advisor_id"] = None


def replace_roles(db: Session, contact: Contact, wanted: Iterable[ContactRole], *, actor_id: uuid.UUID) -> None:
    """Open a row for each role newly held, close the row of each role given
    up. The caller flushes and commits."""

    now = utcnow()
    existing = {
        row.role: row
        for row in db.scalars(
            select(ContactRoleAssignment).where(ContactRoleAssignment.contact_id == contact.id, ContactRoleAssignment.valid_to.is_(None))
        ).all()
    }
    wanted_set = set(wanted)
    for role in sorted(wanted_set - existing.keys(), key=lambda r: r.value):
        db.add(
            ContactRoleAssignment(
                group_id=contact.group_id,
                contact_id=contact.id,
                role=role,
                valid_from=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    for role, row in existing.items():
        if role not in wanted_set:
            row.valid_to = now
            row.updated_by = actor_id


def repoint_roles(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[list[str], int]:
    """A merge leaves the survivor holding every role either contact held
    (nothing a merge does loses a role). Every row of the duplicate moves to
    the survivor, its history included; an open row for a role the survivor
    already holds is closed as it moves, so the survivor keeps one open row
    per role. Returns the roles the survivor gained and the rows moved."""

    now = utcnow()
    target_open = set(
        db.scalars(
            select(ContactRoleAssignment.role).where(ContactRoleAssignment.contact_id == target_id, ContactRoleAssignment.valid_to.is_(None))
        ).all()
    )
    gained: list[str] = []
    moved = 0
    for row in db.scalars(select(ContactRoleAssignment).where(ContactRoleAssignment.contact_id == duplicate_id)).all():
        if row.valid_to is None:
            if row.role in target_open:
                row.valid_to = now
            else:
                target_open.add(row.role)
                gained.append(row.role.value)
        row.contact_id = target_id
        moved += 1
    return sorted(gained), moved
