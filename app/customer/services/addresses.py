"""CustomerAddress — multi-valued postal addresses (WP-3 PR-5, ADR-067; split
from customer.py, KAN-617).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import NotFoundError
from app.core.postal_codes import derive_canton
from app.customer.models.customer import (
    Customer,
    CustomerAddress,
)
from app.customer.schemas.customer import (
    CustomerAddressCreate,
    CustomerAddressUpdate,
)
from app.customer.services.contact_channels import (
    _consent_create_kwargs,
    _consent_snapshot,
    _has_usable_primary,
    _prepare_primary_change,
    _stamp_consent_timestamp_on_grant,
    _unset_other_primaries,
)
from app.customer.services.customer_common import _fixup_single_primary, _group_scoped_or_404, _validate_country_codes

# --- CustomerAddress: multi-valued postal addresses (WP-3 PR-5, ADR-067).
# No FR-03 "last contact point" invariant here — that rule is about
# reachability (phone/email), not billing/delivery addresses — so unlike
# phone/email there is no floor on how many a customer must keep.


def _address_audit_payload(address: CustomerAddress) -> dict[str, Any]:
    return {
        "addressType": address.address_type.value,
        "label": address.label,
        "addressStreet": address.address_street,
        "addressLine2": address.address_line2,
        "addressHouseNumber": address.address_house_number,
        "addressPostalCode": address.address_postal_code,
        "addressLocality": address.address_locality,
        "addressCanton": address.address_canton,
        "addressCountry": address.address_country,
        "isPrimary": address.is_primary,
        **_consent_snapshot(address),
    }


def list_customer_addresses(db: Session, *, customer_id: uuid.UUID) -> list[CustomerAddress]:
    stmt = (
        select(CustomerAddress).where(CustomerAddress.customer_id == customer_id).order_by(CustomerAddress.created_at)
    )
    return list(db.scalars(stmt).all())


def get_customer_address_or_404(
    db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID, address_id: uuid.UUID
) -> CustomerAddress:
    address = _group_scoped_or_404(db, CustomerAddress, address_id, group_id)
    if address.customer_id != customer_id:
        raise NotFoundError(f"CustomerAddress {address_id} was not found.")
    return address


def create_customer_address(
    db: Session, *, customer: Customer, data: CustomerAddressCreate, actor_id: uuid.UUID
) -> CustomerAddress:
    _validate_country_codes(db, {"addressCountry": data.address_country})
    is_primary = data.is_primary or not _has_usable_primary(
        db, CustomerAddress, customer_id=customer.id, type_value=data.address_type
    )

    if is_primary:
        _unset_other_primaries(db, CustomerAddress, customer_id=customer.id, type_value=data.address_type)

    address = CustomerAddress(
        group_id=customer.group_id,
        customer_id=customer.id,
        address_type=data.address_type,
        label=data.label,
        address_street=data.address_street,
        address_line2=data.address_line2,
        address_house_number=data.address_house_number,
        address_postal_code=data.address_postal_code,
        address_locality=data.address_locality,
        address_canton=derive_canton(data.address_postal_code, data.address_country),
        address_country=data.address_country,
        is_primary=is_primary,
        created_by=actor_id,
        updated_by=actor_id,
        **_consent_create_kwargs(data),
    )
    db.add(address)
    db.flush()

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="address_add",
        actor_id=actor_id,
        after=_address_audit_payload(address),
    )
    db.commit()
    db.refresh(address)
    return address


def update_customer_address(
    db: Session, *, address: CustomerAddress, data: CustomerAddressUpdate, actor_id: uuid.UUID
) -> CustomerAddress:
    changes = data.model_dump(exclude_unset=True)
    if "address_country" in changes:
        _validate_country_codes(db, {"addressCountry": changes["address_country"]})
    was_granted = address.consent_granted
    before = _address_audit_payload(address)

    # KAN-46: addresses carry no FR-03 "last contact point" floor, but the
    # `address` projection (primary domicile) still needs re-electing when
    # the primary is closed or flagged do_not_use — and electing when a row
    # is reopened (KAN-113); the shared helper decides both.
    groups_to_settle = _prepare_primary_change(db, CustomerAddress, row=address, changes=changes, noun="address")

    for field, value in changes.items():
        setattr(address, field, value)
    if "address_postal_code" in changes or "address_country" in changes:
        address.address_canton = derive_canton(address.address_postal_code, address.address_country)
    _stamp_consent_timestamp_on_grant(address, changes, was_granted=was_granted)
    address.updated_by = actor_id

    db.flush()

    for type_value in groups_to_settle:
        _fixup_single_primary(db, CustomerAddress, customer_id=address.customer_id, type_value=type_value)
    db.flush()

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=address.customer_id,
        tenant_id=address.group_id,
        action="address_update",
        actor_id=actor_id,
        before=before,
        after=_address_audit_payload(address),
    )
    db.commit()
    db.refresh(address)
    return address


def delete_customer_address(db: Session, *, address: CustomerAddress, actor_id: uuid.UUID) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=address.customer_id,
        tenant_id=address.group_id,
        action="address_remove",
        actor_id=actor_id,
        before=_address_audit_payload(address),
    )
    customer_id, address_type = address.customer_id, address.address_type
    db.delete(address)
    db.flush()
    # KAN-46 — see delete_customer_phone.
    _fixup_single_primary(db, CustomerAddress, customer_id=customer_id, type_value=address_type)
    db.commit()
