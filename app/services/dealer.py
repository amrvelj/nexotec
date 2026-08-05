"""Dealer service layer: create/read/update + the audit-logging and
lifecycle rules the spec calls out (license/tax_id/status changes are
audit-logged; `offboarded` is a terminal status).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.models.dealer import Dealer, DealerStatus
from app.schemas.dealer import DealerCreate, DealerUpdate
from app.services.audit import record_audit_event

_AUDITED_FIELDS = {"dealer_license_number", "license_state", "tax_id", "status"}
_SECRET_FIELDS = {"tax_id"}
_TERMINAL_DEALER_STATUSES = {DealerStatus.OFFBOARDED}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _redact(field: str, value: Any) -> Any:
    if field in _SECRET_FIELDS and value is not None:
        return "***redacted***"
    return _plain(value)


def get_dealer_or_404(db: Session, dealer_id: uuid.UUID) -> Dealer:
    dealer = db.get(Dealer, dealer_id)
    if dealer is None:
        raise NotFoundError(f"Dealer {dealer_id} was not found.")
    return dealer


def list_dealers(
    db: Session,
    *,
    params: PageParams,
    status: DealerStatus | None,
    parent_group_id: uuid.UUID | None,
) -> tuple[list[Dealer], str | None]:
    stmt = select(Dealer)
    if status is not None:
        stmt = stmt.where(Dealer.status == status)
    if parent_group_id is not None:
        stmt = stmt.where(Dealer.parent_group_id == parent_group_id)
    stmt = paginate_query(stmt, model=Dealer, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def create_dealer(db: Session, *, data: DealerCreate, actor_id: uuid.UUID) -> Dealer:
    dealer = Dealer(
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
    db.add(dealer)
    db.flush()

    record_audit_event(
        db,
        entity_type="dealer",
        entity_id=dealer.id,
        tenant_id=dealer.id,
        action="create",
        actor_id=actor_id,
        after={
            "legal_name": dealer.legal_name,
            "dealer_license_number": dealer.dealer_license_number,
            "license_state": dealer.license_state,
            "tax_id": _redact("tax_id", dealer.tax_id),
            "status": _plain(dealer.status),
        },
    )
    db.commit()
    db.refresh(dealer)
    return dealer


def update_dealer(db: Session, *, dealer: Dealer, data: DealerUpdate, actor_id: uuid.UUID) -> Dealer:
    changes = data.model_dump(exclude_unset=True, exclude={"address"})

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if dealer.status in _TERMINAL_DEALER_STATUSES and new_status != dealer.status:
            raise ConflictError(
                f"Dealer status '{dealer.status.value}' is terminal and cannot be changed.",
                details={"currentStatus": dealer.status.value},
            )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(dealer, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _redact(field, current)
            after[field] = _redact(field, value)
        setattr(dealer, field, value)

    if data.address is not None:
        dealer.address_street = data.address.street
        dealer.address_house_number = data.address.house_number
        dealer.address_postal_code = data.address.postal_code
        dealer.address_locality = data.address.locality
        dealer.address_canton = data.address.canton
        dealer.address_country = data.address.country

    dealer.updated_by = actor_id
    dealer.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="dealer",
            entity_id=dealer.id,
            tenant_id=dealer.id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    db.commit()
    db.refresh(dealer)
    return dealer
