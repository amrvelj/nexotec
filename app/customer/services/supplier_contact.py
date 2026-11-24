"""A contact as Stock's purchase booking reads it — its supplier (KAN-465).

PRD-Stock: a stock item's `supplier` is a reference + label; Anto settled on
2026-10-26 that it points at a contact with the supplier role (KAN-464), and
on 2026-11-21 (option A) that every new booking names one. Stock never reads
this context's tables: it calls `supplier_contact` (customer.public), which
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

from app.customer.models.customer import ContactRole, Customer, CustomerLifecycleStatus, CustomerRole, CustomerType
from app.customer.services.customer_refs import customer_display_name, get_customer_or_404


@dataclasses.dataclass(frozen=True)
class SupplierContact:
    id: uuid.UUID
    label: str
    holds_supplier_role: bool
    merged: bool
    # True / False read from the contact; None when nobody can tell (an
    # unconfirmed business).
    vat_registered_prefill: bool | None


def vat_registered_prefill(*, customer_type: CustomerType, vat_registered: bool, vat_status_confirmed: bool) -> bool | None:
    if vat_status_confirmed:
        return vat_registered
    if customer_type == CustomerType.INDIVIDUAL:
        return False
    return None


def supplier_contact(db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID) -> SupplierContact | None:
    """The contact `customer_id` names in `group_id`, or None when it names
    none there (another group's included, rule 7). SELECTs only."""

    customer = db.get(Customer, customer_id)
    if customer is None or customer.group_id != group_id:
        return None
    ref = get_customer_or_404(db, group_id, customer_id)
    holds = (
        db.scalar(
            select(CustomerRole.id).where(
                CustomerRole.customer_id == customer_id,
                CustomerRole.role == ContactRole.SUPPLIER,
                CustomerRole.valid_to.is_(None),
            )
        )
        is not None
    )
    return SupplierContact(
        id=customer.id,
        label=customer_display_name(ref),
        holds_supplier_role=holds,
        merged=customer.lifecycle_status == CustomerLifecycleStatus.MERGED,
        vat_registered_prefill=vat_registered_prefill(
            customer_type=customer.customer_type,
            vat_registered=customer.vat_registered,
            vat_status_confirmed=customer.vat_status_confirmed,
        ),
    )
