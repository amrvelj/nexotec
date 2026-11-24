"""ContactExternalId — per-dealer CRM/OEM linkage, platform_admin-write (split
from contact.py, KAN-617).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.contact.models.contact import (
    Contact,
    ContactExternalId,
)
from app.contact.schemas.contact import (
    ContactExternalIdCreate,
    ContactExternalIdUpdate,
)
from app.contact.services.contact_common import _group_scoped_or_404, _violated_constraint

# --- ContactExternalId: per-dealer CRM/OEM linkage, platform_admin-write
# only (authorization enforced at the API layer, same convention as every
# other role-gated route in this codebase — the service layer does the CRUD,
# not the access check).


def list_contact_external_ids(db: Session, *, contact_id: uuid.UUID) -> list[ContactExternalId]:
    stmt = (
        select(ContactExternalId)
        .where(ContactExternalId.contact_id == contact_id)
        .order_by(ContactExternalId.created_at)
    )
    return list(db.scalars(stmt).all())


def get_contact_external_id_or_404(
    db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID, external_id_row_id: uuid.UUID
) -> ContactExternalId:
    row = _group_scoped_or_404(db, ContactExternalId, external_id_row_id, group_id)
    if row.contact_id != contact_id:
        raise NotFoundError(f"ContactExternalId {external_id_row_id} was not found.")
    return row


def create_contact_external_id(
    db: Session,
    *,
    contact: Contact,
    data: ContactExternalIdCreate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> ContactExternalId:
    row = ContactExternalId(
        group_id=contact.group_id,
        contact_id=contact.id,
        system_name=data.system_name,
        external_id=data.external_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This system already has an external ID on this contact, or this external ID is already"
            " used under this system for another contact in this dealer.",
            details={"reason": "external_id_taken", "systemName": data.system_name, "externalId": data.external_id},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=contact.id,
        tenant_id=contact.group_id,
        action="external_id_add",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        after={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.commit()
    db.refresh(row)
    return row


_EXTERNAL_ID_UNIQUE_CONSTRAINTS = frozenset(
    {"uq_contact_external_id_group_system_external", "uq_contact_external_id_contact_system"}
)


def update_contact_external_id(
    db: Session,
    *,
    row: ContactExternalId,
    data: ContactExternalIdUpdate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> ContactExternalId:
    changes = data.model_dump(exclude_unset=True)
    before = {"systemName": row.system_name, "externalId": row.external_id}

    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_by = actor_id

    # The refusal names the pair the PATCH tried to write (KAN-481): after the
    # rollback the row reloads its stored values, so they are taken from the
    # body here, with the stored value for a key the PATCH left out.
    attempted = {
        "systemName": changes.get("system_name", before["systemName"]),
        "externalId": changes.get("external_id", before["externalId"]),
    }
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Only the two unique constraints are a taken pair (KAN-483); any
        # other integrity failure stays loud — a NOT NULL violation names no
        # constraint at all. SQLite's driver reports no diagnostics, so there
        # every failure is still read as the pair.
        if getattr(exc.orig, "diag", None) is not None and _violated_constraint(exc) not in (
            _EXTERNAL_ID_UNIQUE_CONSTRAINTS
        ):
            raise
        raise ConflictError(
            "This system already has an external ID on this contact, or this external ID is already"
            " used under this system for another contact in this dealer.",
            details={"reason": "external_id_taken", **attempted},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=row.contact_id,
        tenant_id=row.group_id,
        action="external_id_update",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before=before,
        after={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_contact_external_id(
    db: Session,
    *,
    row: ContactExternalId,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=row.contact_id,
        tenant_id=row.group_id,
        action="external_id_remove",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.delete(row)
    db.commit()
