"""A contact as Stock's purchase booking reads it — its supplier (KAN-465).

PRD-Stock: a stock item's `supplier` is a reference + label; Anto settled on
2026-10-26 that it points at a contact with the supplier role (KAN-464), and
on 2026-11-21 (option A) that every new booking names one. Stock never reads
this context's tables: it calls `supplier_contact` (contact.public), which
answers with plain values.

The VAT-registration prefill (PRD-Stock, fiktiver Vorsteuerabzug): a contact
whose VAT status someone confirmed (KAN-295) says it; an unconfirmed private
individual is not VAT-registered; an unconfirmed business is unknown (None),
so the booking user decides. The purchase dialog applies the same rule to the
contact it shows (`supplierVatPrefill`, frontend), and the booking audits a
departure from it.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.models.contact import Contact, ContactLifecycleStatus, ContactRole, ContactRoleAssignment, ContactType
from app.contact.services.contact_refs import contact_display_name, get_contact_or_404


@dataclasses.dataclass(frozen=True)
class SupplierContact:
    id: uuid.UUID
    label: str
    holds_supplier_role: bool
    merged: bool
    # True / False read from the contact; None when nobody can tell (an
    # unconfirmed business).
    vat_registered_prefill: bool | None


def vat_registered_prefill(*, contact_type: ContactType, vat_registered: bool, vat_status_confirmed: bool) -> bool | None:
    if vat_status_confirmed:
        return vat_registered
    if contact_type == ContactType.INDIVIDUAL:
        return False
    return None


def supplier_contact(db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID) -> SupplierContact | None:
    """The contact `contact_id` names in `group_id`, or None when it names
    none there (another group's included, rule 7). SELECTs only."""

    contact = db.get(Contact, contact_id)
    if contact is None or contact.group_id != group_id:
        return None
    ref = get_contact_or_404(db, group_id, contact_id)
    holds = (
        db.scalar(
            select(ContactRoleAssignment.id).where(
                ContactRoleAssignment.contact_id == contact_id,
                ContactRoleAssignment.role == ContactRole.SUPPLIER,
                ContactRoleAssignment.valid_to.is_(None),
            )
        )
        is not None
    )
    return SupplierContact(
        id=contact.id,
        label=contact_display_name(ref),
        holds_supplier_role=holds,
        merged=contact.lifecycle_status == ContactLifecycleStatus.MERGED,
        vat_registered_prefill=vat_registered_prefill(
            contact_type=contact.contact_type,
            vat_registered=contact.vat_registered,
            vat_status_confirmed=contact.vat_status_confirmed,
        ),
    )
