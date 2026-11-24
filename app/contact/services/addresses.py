"""ContactAddress — multi-valued postal addresses (WP-3 PR-5, ADR-067; split
from contact.py, KAN-617).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    Contact,
    ContactAddress,
)
from app.contact.schemas.contact import (
    ContactAddressCreate,
    ContactAddressUpdate,
)
from app.contact.services.contact_channels import (
    _consent_create_kwargs,
    _consent_snapshot,
    _has_usable_primary,
    _prepare_primary_change,
    _stamp_consent_timestamp_on_grant,
    _unset_other_primaries,
)
from app.contact.services.contact_common import _fixup_single_primary, _group_scoped_or_404, _validate_country_codes
from app.core.audit import record_audit_event
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.postal_codes import derive_canton
from app.core.validators import validate_postal_code_for_country

# --- ContactAddress: multi-valued postal addresses (WP-3 PR-5, ADR-067).
# No FR-03 "last contact point" invariant here — that rule is about
# reachability (phone/email), not billing/delivery addresses — so unlike
# phone/email there is no floor on how many a contact must keep.


def _address_audit_payload(address: ContactAddress) -> dict[str, Any]:
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


def list_contact_addresses(db: Session, *, contact_id: uuid.UUID) -> list[ContactAddress]:
    stmt = (
        select(ContactAddress).where(ContactAddress.contact_id == contact_id).order_by(ContactAddress.created_at)
    )
    return list(db.scalars(stmt).all())


def get_contact_address_or_404(
    db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID, address_id: uuid.UUID
) -> ContactAddress:
    address = _group_scoped_or_404(db, ContactAddress, address_id, group_id)
    if address.contact_id != contact_id:
        raise NotFoundError(f"ContactAddress {address_id} was not found.")
    return address


def create_contact_address(
    db: Session,
    *,
    contact: Contact,
    data: ContactAddressCreate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> ContactAddress:
    _validate_country_codes(db, {"addressCountry": data.address_country})
    is_primary = data.is_primary or not _has_usable_primary(
        db, ContactAddress, contact_id=contact.id, type_value=data.address_type
    )

    if is_primary:
        _unset_other_primaries(db, ContactAddress, contact_id=contact.id, type_value=data.address_type)

    address = ContactAddress(
        group_id=contact.group_id,
        contact_id=contact.id,
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
        entity_type="contact",
        entity_id=contact.id,
        tenant_id=contact.group_id,
        action="address_add",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        after=_address_audit_payload(address),
    )
    db.commit()
    db.refresh(address)
    return address


def _assert_merged_postal_code(address: ContactAddress, changes: dict[str, Any]) -> None:
    """KAN-779: a PATCH judges the postal code it would store against the country it would
    store — the create path's rule (D-11, ``ContactAddressCreate``), applied to the merged row.
    Judged only when the PATCH touches the code or the country, so a stored row that predates
    the rule still saves an unrelated field (as Locations do, ``_assert_address_block``)."""

    if "address_postal_code" not in changes and "address_country" not in changes:
        return
    # A null for either never reaches here: the schema refuses it (KAN-789).
    postal_code = changes.get("address_postal_code", address.address_postal_code)
    country = changes.get("address_country", address.address_country)
    try:
        validate_postal_code_for_country(postal_code, country)
    except ValueError as exc:
        raise UnprocessableEntityError(
            str(exc), details={"reason": "contact_address_postal_code_invalid"}
        ) from exc


def update_contact_address(
    db: Session,
    *,
    address: ContactAddress,
    data: ContactAddressUpdate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> ContactAddress:
    changes = data.model_dump(exclude_unset=True)
    if "address_country" in changes:
        _validate_country_codes(db, {"addressCountry": changes["address_country"]})
    _assert_merged_postal_code(address, changes)
    was_granted = address.consent_granted
    before = _address_audit_payload(address)

    # KAN-46: addresses carry no FR-03 "last contact point" floor, but the
    # `address` projection (primary domicile) still needs re-electing when
    # the primary is closed or flagged do_not_use — and electing when a row
    # is reopened (KAN-113); the shared helper decides both.
    groups_to_settle = _prepare_primary_change(db, ContactAddress, row=address, changes=changes, noun="address")

    for field, value in changes.items():
        setattr(address, field, value)
    if "address_postal_code" in changes or "address_country" in changes:
        address.address_canton = derive_canton(address.address_postal_code, address.address_country)
    _stamp_consent_timestamp_on_grant(address, changes, was_granted=was_granted)
    address.updated_by = actor_id

    db.flush()

    for type_value in groups_to_settle:
        _fixup_single_primary(db, ContactAddress, contact_id=address.contact_id, type_value=type_value)
    db.flush()

    record_audit_event(
        db,
        entity_type="contact",
        entity_id=address.contact_id,
        tenant_id=address.group_id,
        action="address_update",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before=before,
        after=_address_audit_payload(address),
    )
    db.commit()
    db.refresh(address)
    return address


def delete_contact_address(
    db: Session,
    *,
    address: ContactAddress,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> None:
    record_audit_event(
        db,
        entity_type="contact",
        entity_id=address.contact_id,
        tenant_id=address.group_id,
        action="address_remove",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before=_address_audit_payload(address),
    )
    contact_id, address_type = address.contact_id, address.address_type
    db.delete(address)
    db.flush()
    # KAN-46 — see delete_contact_phone.
    _fixup_single_primary(db, ContactAddress, contact_id=contact_id, type_value=address_type)
    db.commit()
