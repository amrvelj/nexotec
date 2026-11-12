"""CustomerExternalId — per-dealer CRM/OEM linkage, platform_admin-write (split
from customer.py, KAN-617).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.customer.models.customer import (
    Customer,
    CustomerExternalId,
)
from app.customer.schemas.customer import (
    CustomerExternalIdCreate,
    CustomerExternalIdUpdate,
)
from app.customer.services.customer_common import _group_scoped_or_404, _violated_constraint

# --- CustomerExternalId: per-dealer CRM/OEM linkage, platform_admin-write
# only (authorization enforced at the API layer, same convention as every
# other role-gated route in this codebase — the service layer does the CRUD,
# not the access check).


def list_customer_external_ids(db: Session, *, customer_id: uuid.UUID) -> list[CustomerExternalId]:
    stmt = (
        select(CustomerExternalId)
        .where(CustomerExternalId.customer_id == customer_id)
        .order_by(CustomerExternalId.created_at)
    )
    return list(db.scalars(stmt).all())


def get_customer_external_id_or_404(
    db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID, external_id_row_id: uuid.UUID
) -> CustomerExternalId:
    row = _group_scoped_or_404(db, CustomerExternalId, external_id_row_id, group_id)
    if row.customer_id != customer_id:
        raise NotFoundError(f"CustomerExternalId {external_id_row_id} was not found.")
    return row


def create_customer_external_id(
    db: Session, *, customer: Customer, data: CustomerExternalIdCreate, actor_id: uuid.UUID
) -> CustomerExternalId:
    row = CustomerExternalId(
        group_id=customer.group_id,
        customer_id=customer.id,
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
            "This system already has an external ID on this customer, or this external ID is already"
            " used under this system for another customer in this dealer.",
            details={"reason": "external_id_taken", "systemName": data.system_name, "externalId": data.external_id},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="external_id_add",
        actor_id=actor_id,
        after={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.commit()
    db.refresh(row)
    return row


_EXTERNAL_ID_UNIQUE_CONSTRAINTS = frozenset(
    {"uq_customer_external_id_group_system_external", "uq_customer_external_id_customer_system"}
)


def update_customer_external_id(
    db: Session, *, row: CustomerExternalId, data: CustomerExternalIdUpdate, actor_id: uuid.UUID
) -> CustomerExternalId:
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
            "This system already has an external ID on this customer, or this external ID is already"
            " used under this system for another customer in this dealer.",
            details={"reason": "external_id_taken", **attempted},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=row.customer_id,
        tenant_id=row.group_id,
        action="external_id_update",
        actor_id=actor_id,
        before=before,
        after={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.commit()
    db.refresh(row)
    return row


def delete_customer_external_id(db: Session, *, row: CustomerExternalId, actor_id: uuid.UUID) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=row.customer_id,
        tenant_id=row.group_id,
        action="external_id_remove",
        actor_id=actor_id,
        before={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.delete(row)
    db.commit()
