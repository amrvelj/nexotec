"""Dealership service layer: create/read/update + the audit-logging and
lifecycle rules the spec calls out (license/tax_id/status changes are
audit-logged; `offboarded` is a terminal status).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.redact import REDACTED_PLACEHOLDER, is_secret_field
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus
from app.platform.schemas.dealership import DealershipCreate, DealershipUpdate

_AUDITED_FIELDS = {"dealer_license_number", "license_state", "tax_id", "status"}
_TERMINAL_DEALERSHIP_STATUSES = {DealershipStatus.OFFBOARDED}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _redact(field: str, value: Any) -> Any:
    if is_secret_field(field) and value is not None:
        return REDACTED_PLACEHOLDER
    return _plain(value)


def get_dealership_or_404(db: Session, dealership_id: uuid.UUID) -> Dealership:
    dealership = db.get(Dealership, dealership_id)
    if dealership is None:
        raise NotFoundError(f"Dealership {dealership_id} was not found.")
    return dealership


def list_dealerships(
    db: Session,
    *,
    params: PageParams,
    status: DealershipStatus | None,
    dealer_group_id: uuid.UUID | None,
) -> tuple[list[Dealership], str | None]:
    stmt = select(Dealership)
    if status is not None:
        stmt = stmt.where(Dealership.status == status)
    if dealer_group_id is not None:
        stmt = stmt.where(Dealership.dealer_group_id == dealer_group_id)
    stmt = paginate_query(stmt, model=Dealership, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def create_dealership(db: Session, *, data: DealershipCreate, actor_id: uuid.UUID) -> Dealership:
    dealer_group_id = data.dealer_group_id
    if dealer_group_id is None:
        # No group named — this dealership starts as a group of one (the
        # common case: onboarding a standalone dealer), same shape every
        # existing dealership was backfilled into.
        group = DealerGroup(name=data.legal_name, created_by=actor_id, updated_by=actor_id)
        db.add(group)
        db.flush()
        dealer_group_id = group.id

    dealership = Dealership(
        dealer_group_id=dealer_group_id,
        legal_name=data.legal_name,
        dba_name=data.dba_name,
        dealer_license_number=data.dealer_license_number,
        license_state=data.license_state,
        franchise_type=data.franchise_type,
        oem_affiliations=data.oem_affiliations,
        address_street=data.address.street,
        address_house_number=data.address.house_number,
        address_postal_code=data.address.postal_code,
        address_locality=data.address.locality,
        address_canton=data.address.canton,
        address_country=data.address.country,
        phone=data.phone,
        tax_id=data.tax_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(dealership)
    db.flush()

    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="create",
        actor_id=actor_id,
        after={
            "legal_name": dealership.legal_name,
            "dealer_license_number": dealership.dealer_license_number,
            "license_state": dealership.license_state,
            "tax_id": _redact("tax_id", dealership.tax_id),
            "status": _plain(dealership.status),
        },
    )
    db.commit()
    db.refresh(dealership)
    return dealership


def update_dealership(
    db: Session, *, dealership: Dealership, data: DealershipUpdate, actor_id: uuid.UUID
) -> Dealership:
    changes = data.model_dump(exclude_unset=True, exclude={"address"})

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if dealership.status in _TERMINAL_DEALERSHIP_STATUSES and new_status != dealership.status:
            raise ConflictError(
                f"Dealership status '{dealership.status.value}' is terminal and cannot be changed.",
                details={"currentStatus": dealership.status.value},
            )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(dealership, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _redact(field, current)
            after[field] = _redact(field, value)
        setattr(dealership, field, value)

    if data.address is not None:
        dealership.address_street = data.address.street
        dealership.address_house_number = data.address.house_number
        dealership.address_postal_code = data.address.postal_code
        dealership.address_locality = data.address.locality
        dealership.address_canton = data.address.canton
        dealership.address_country = data.address.country

    dealership.updated_by = actor_id
    dealership.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="dealership",
            entity_id=dealership.id,
            tenant_id=dealership.id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    db.commit()
    db.refresh(dealership)
    return dealership
