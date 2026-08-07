"""Customer service layer: tenant-scoped CRUD, duplicate-check typeahead,
and merge. PII changes (name/email/phone/address) and lifecycle_status
transitions are audit-logged with before/after (spec: "Every change to PII
... is audit-logged"; lifecycle_status added for the same accountability
reason Dealer/User audit their status fields).
"""

import uuid
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError, ConflictError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.tenancy import get_or_404
from app.models.customer import Customer, CustomerLifecycleStatus
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.services.audit import record_audit_event

_PII_FIELDS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "address_street",
    "address_house_number",
    "address_postal_code",
    "address_locality",
    "address_canton",
    "address_country",
}
_AUDITED_FIELDS = _PII_FIELDS | {"lifecycle_status"}
_TERMINAL_LIFECYCLE_STATUSES = {CustomerLifecycleStatus.MERGED}
_DUPLICATE_CHECK_LIMIT = 10


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    return get_or_404(db, Customer, customer_id, tenant_id)


def list_customers(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    q: str | None,
    lifecycle_status: CustomerLifecycleStatus | None,
    updated_since,
    params: PageParams,
) -> tuple[list[Customer], str | None]:
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Customer.first_name.ilike(pattern),
                Customer.last_name.ilike(pattern),
                Customer.email.ilike(pattern),
                Customer.phone.ilike(pattern),
            )
        )
    if lifecycle_status is not None:
        stmt = stmt.where(Customer.lifecycle_status == lifecycle_status)
    if updated_since is not None:
        stmt = stmt.where(Customer.updated_at >= updated_since)
    stmt = paginate_query(stmt, model=Customer, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def duplicate_check(db: Session, *, tenant_id: uuid.UUID, q: str) -> list[Customer]:
    """Advisory typeahead (Swiss addendum Round 2 #5) — not a blocking gate
    on create. Ranks exact email/phone matches above partial name/contact
    matches, capped to a small result set for a per-keystroke UI call.
    """

    pattern = f"%{q}%"
    score = case(
        (Customer.email == q, 3),
        (Customer.phone == q, 3),
        (
            or_(Customer.first_name.ilike(f"{q}%"), Customer.last_name.ilike(f"{q}%")),
            2,
        ),
        else_=1,
    )
    stmt = (
        select(Customer)
        .where(
            Customer.tenant_id == tenant_id,
            or_(
                Customer.first_name.ilike(pattern),
                Customer.last_name.ilike(pattern),
                Customer.email.ilike(pattern),
                Customer.phone.ilike(pattern),
            ),
        )
        .order_by(score.desc(), Customer.last_name.asc(), Customer.first_name.asc())
        .limit(_DUPLICATE_CHECK_LIMIT)
    )
    return list(db.scalars(stmt).all())


def create_customer(db: Session, *, tenant_id: uuid.UUID, data: CustomerCreate, actor_id: uuid.UUID) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        customer_type=data.customer_type,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        phone=data.phone,
        preferred_contact_method=data.preferred_contact_method,
        lifecycle_status=data.lifecycle_status,
        source=data.source,
        source_ref=data.source_ref,
        marketing_consent=data.marketing_consent,
        created_by=actor_id,
        updated_by=actor_id,
    )
    if data.address is not None:
        customer.address_street = data.address.street
        customer.address_house_number = data.address.house_number
        customer.address_postal_code = data.address.postal_code
        customer.address_locality = data.address.locality
        # No canton on CustomerAddress (unlike Dealer's) — column stays
        # nullable and simply unset from this schema going forward.
        customer.address_canton = None
        customer.address_country = data.address.country

    db.add(customer)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A customer with this email address already exists for this dealer.",
            details={"email": data.email},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=tenant_id,
        action="create",
        actor_id=actor_id,
        after={field: _plain(getattr(customer, field)) for field in _AUDITED_FIELDS},
    )
    db.commit()
    db.refresh(customer)
    return customer


def update_customer(db: Session, *, customer: Customer, data: CustomerUpdate, actor_id: uuid.UUID) -> Customer:
    if customer.lifecycle_status in _TERMINAL_LIFECYCLE_STATUSES:
        raise ConflictError(
            f"Customer lifecycle_status '{customer.lifecycle_status.value}' is terminal and cannot be changed"
            " via PATCH.",
            details={"currentLifecycleStatus": customer.lifecycle_status.value},
        )

    changes = data.model_dump(exclude_unset=True, exclude={"address"})

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(customer, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _plain(current)
            after[field] = _plain(value)
        setattr(customer, field, value)

    if "address" in data.model_fields_set:
        address_fields = {
            "address_street": data.address.street if data.address else None,
            "address_house_number": data.address.house_number if data.address else None,
            "address_postal_code": data.address.postal_code if data.address else None,
            "address_locality": data.address.locality if data.address else None,
            "address_canton": None,
            "address_country": data.address.country if data.address else None,
        }
        for field, value in address_fields.items():
            current = getattr(customer, field)
            if current == value:
                continue
            before[field] = current
            after[field] = value
            setattr(customer, field, value)

    if not customer.email and not customer.phone:
        raise BadRequestError("At least one of email or phone is required.")

    customer.updated_by = actor_id
    customer.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=customer.tenant_id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "A customer with this email address already exists for this dealer.",
            details={"email": changes.get("email")},
        ) from exc
    db.refresh(customer)
    return customer


def merge_customer(
    db: Session, *, customer: Customer, duplicate_of_customer_id: uuid.UUID, actor_id: uuid.UUID
) -> Customer:
    if duplicate_of_customer_id == customer.id:
        raise BadRequestError("A customer cannot be merged into itself.")

    target = get_or_404(db, Customer, duplicate_of_customer_id, customer.tenant_id)
    if target.lifecycle_status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Cannot merge into a customer that has itself been merged.",
            details={"duplicateOfCustomerId": str(duplicate_of_customer_id)},
        )
    if customer.lifecycle_status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Customer has already been merged.", details={"currentLifecycleStatus": "merged"}
        )

    before = {"lifecycleStatus": customer.lifecycle_status.value, "duplicateOfCustomerId": None}
    customer.lifecycle_status = CustomerLifecycleStatus.MERGED
    customer.duplicate_of_customer_id = duplicate_of_customer_id
    customer.updated_by = actor_id
    customer.version += 1

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.tenant_id,
        action="merge",
        actor_id=actor_id,
        before=before,
        after={"lifecycleStatus": "merged", "duplicateOfCustomerId": str(duplicate_of_customer_id)},
    )
    db.commit()
    db.refresh(customer)
    return customer
