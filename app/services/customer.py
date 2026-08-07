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

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.core.tenancy import get_or_404
from app.models.customer import (
    Customer,
    CustomerEmail,
    CustomerExternalId,
    CustomerLifecycleStatus,
    CustomerPhone,
    CustomerType,
)
from app.schemas.customer import (
    CustomerCreate,
    CustomerEmailCreate,
    CustomerEmailUpdate,
    CustomerExternalIdCreate,
    CustomerExternalIdUpdate,
    CustomerPhoneCreate,
    CustomerPhoneUpdate,
    CustomerUpdate,
)
from app.services.audit import record_audit_event

_PII_FIELDS = {
    "first_name",
    "last_name",
    "birth_date",
    "nationality",
    "company_name",
    "legal_form",
    "tax_id",
    "email",
    "phone",
    "address_street",
    "address_house_number",
    "address_postal_code",
    "address_locality",
    "address_canton",
    "address_country",
}
_AUDITED_FIELDS = _PII_FIELDS | {"lifecycle_status", "preferred_channel"}
_TERMINAL_LIFECYCLE_STATUSES = {CustomerLifecycleStatus.MERGED}
_DUPLICATE_CHECK_LIMIT = 10
# Never logged in plaintext, same as Dealer.tax_id — see services/dealer.py's
# identical _redact pattern.
_SECRET_FIELDS = {"tax_id"}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _redact(field: str, value: Any) -> Any:
    if field in _SECRET_FIELDS and value is not None:
        return "***redacted***"
    return _plain(value)


_INDIVIDUAL_ONLY_FIELDS = {"first_name", "last_name", "birth_date", "nationality"}
_BUSINESS_ONLY_FIELDS = {"company_name", "legal_form", "tax_id"}


def _validate_customer_type_fields_on_update(customer: Customer, changes: dict[str, Any]) -> None:
    """customer_type is immutable and not part of CustomerUpdate (see schema
    docstring) — check any individual/business-only fields present in this
    PATCH against the customer's existing, unchanging type.
    """

    forbidden = _BUSINESS_ONLY_FIELDS if customer.customer_type == CustomerType.INDIVIDUAL else _INDIVIDUAL_ONLY_FIELDS
    label = "business-only" if customer.customer_type == CustomerType.INDIVIDUAL else "individual-only"
    for field in forbidden:
        if changes.get(field) is not None:
            raise BadRequestError(
                f"'{field}' is a {label} field and cannot be set on a {customer.customer_type.value} customer."
            )


def get_customer_or_404(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    return get_or_404(db, Customer, customer_id, tenant_id)


def get_customer_by_id_or_404(db: Session, customer_id: uuid.UUID) -> Customer:
    """Tenant-agnostic lookup — platform_admin only, see the call sites in
    app/api/v1/customers.py's CustomerExternalId write endpoints for why
    Customer needs this one deliberate exception to its usual
    "platform_admin has no cross-tenant reach here" rule.
    """

    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"Customer {customer_id} was not found.")
    return customer


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
        birth_date=data.birth_date,
        nationality=data.nationality,
        company_name=data.company_name,
        legal_form=data.legal_form,
        tax_id=data.tax_id,
        preferred_channel=data.preferred_channel,
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
        after={field: _redact(field, getattr(customer, field)) for field in _AUDITED_FIELDS},
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
    _validate_customer_type_fields_on_update(customer, changes)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(customer, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _redact(field, current)
            after[field] = _redact(field, value)
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


# --- CustomerPhone / CustomerEmail: multi-valued contact details (Customer
# PRD, 2026-08-07). is_primary is enforced here, not a DB constraint (CTO
# ruling: not a high-contention field) — exactly one true per customer per
# table, auto-set on the first entry, previous primary unset in the same
# transaction when a new one is marked primary. Audited under the parent
# Customer's entity_type/entity_id, same pattern as Vehicle's custody events.


def list_customer_phones(db: Session, *, customer_id: uuid.UUID) -> list[CustomerPhone]:
    stmt = select(CustomerPhone).where(CustomerPhone.customer_id == customer_id).order_by(CustomerPhone.created_at)
    return list(db.scalars(stmt).all())


def get_customer_phone_or_404(
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, phone_id: uuid.UUID
) -> CustomerPhone:
    phone = get_or_404(db, CustomerPhone, phone_id, tenant_id)
    if phone.customer_id != customer_id:
        raise NotFoundError(f"CustomerPhone {phone_id} was not found.")
    return phone


def create_customer_phone(
    db: Session, *, customer: Customer, data: CustomerPhoneCreate, actor_id: uuid.UUID
) -> CustomerPhone:
    is_first = db.scalar(select(CustomerPhone).where(CustomerPhone.customer_id == customer.id)) is None
    is_primary = data.is_primary or is_first

    if is_primary:
        _unset_other_primaries(db, CustomerPhone, customer_id=customer.id)

    phone = CustomerPhone(
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        phone_type=data.phone_type,
        phone_e164=data.phone_e164,
        is_primary=is_primary,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(phone)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This phone number is already on this customer.", details={"phoneE164": data.phone_e164}
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.tenant_id,
        action="phone_add",
        actor_id=actor_id,
        after={"phoneType": phone.phone_type.value, "phoneE164": phone.phone_e164, "isPrimary": phone.is_primary},
    )
    db.commit()
    db.refresh(phone)
    return phone


def update_customer_phone(
    db: Session, *, phone: CustomerPhone, data: CustomerPhoneUpdate, actor_id: uuid.UUID
) -> CustomerPhone:
    changes = data.model_dump(exclude_unset=True)
    before = {"phoneType": phone.phone_type.value, "phoneE164": phone.phone_e164, "isPrimary": phone.is_primary}

    if changes.get("is_primary") is True and not phone.is_primary:
        _unset_other_primaries(db, CustomerPhone, customer_id=phone.customer_id)
    elif changes.get("is_primary") is False and phone.is_primary:
        raise BadRequestError(
            "Cannot unset the primary phone directly — mark a different phone as primary instead."
        )

    for field, value in changes.items():
        setattr(phone, field, value)
    phone.updated_by = actor_id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This phone number is already on this customer.", details={"phoneE164": changes.get("phone_e164")}
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=phone.customer_id,
        tenant_id=phone.tenant_id,
        action="phone_update",
        actor_id=actor_id,
        before=before,
        after={"phoneType": phone.phone_type.value, "phoneE164": phone.phone_e164, "isPrimary": phone.is_primary},
    )
    db.commit()
    db.refresh(phone)
    return phone


def delete_customer_phone(db: Session, *, phone: CustomerPhone, actor_id: uuid.UUID) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=phone.customer_id,
        tenant_id=phone.tenant_id,
        action="phone_remove",
        actor_id=actor_id,
        before={"phoneType": phone.phone_type.value, "phoneE164": phone.phone_e164, "isPrimary": phone.is_primary},
    )
    db.delete(phone)
    db.commit()


def list_customer_emails(db: Session, *, customer_id: uuid.UUID) -> list[CustomerEmail]:
    stmt = select(CustomerEmail).where(CustomerEmail.customer_id == customer_id).order_by(CustomerEmail.created_at)
    return list(db.scalars(stmt).all())


def get_customer_email_or_404(
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, email_id: uuid.UUID
) -> CustomerEmail:
    email = get_or_404(db, CustomerEmail, email_id, tenant_id)
    if email.customer_id != customer_id:
        raise NotFoundError(f"CustomerEmail {email_id} was not found.")
    return email


def create_customer_email(
    db: Session, *, customer: Customer, data: CustomerEmailCreate, actor_id: uuid.UUID
) -> CustomerEmail:
    is_first = db.scalar(select(CustomerEmail).where(CustomerEmail.customer_id == customer.id)) is None
    is_primary = data.is_primary or is_first

    if is_primary:
        _unset_other_primaries(db, CustomerEmail, customer_id=customer.id)

    email = CustomerEmail(
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        email_type=data.email_type,
        email_address=data.email_address,
        is_primary=is_primary,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(email)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This email address is already on this customer.", details={"emailAddress": data.email_address}
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.tenant_id,
        action="email_add",
        actor_id=actor_id,
        after={
            "emailType": email.email_type.value,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
        },
    )
    db.commit()
    db.refresh(email)
    return email


def update_customer_email(
    db: Session, *, email: CustomerEmail, data: CustomerEmailUpdate, actor_id: uuid.UUID
) -> CustomerEmail:
    changes = data.model_dump(exclude_unset=True)
    before = {
        "emailType": email.email_type.value,
        "emailAddress": email.email_address,
        "isPrimary": email.is_primary,
    }

    if changes.get("is_primary") is True and not email.is_primary:
        _unset_other_primaries(db, CustomerEmail, customer_id=email.customer_id)
    elif changes.get("is_primary") is False and email.is_primary:
        raise BadRequestError(
            "Cannot unset the primary email directly — mark a different email as primary instead."
        )

    for field, value in changes.items():
        setattr(email, field, value)
    email.updated_by = actor_id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This email address is already on this customer.",
            details={"emailAddress": changes.get("email_address")},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=email.customer_id,
        tenant_id=email.tenant_id,
        action="email_update",
        actor_id=actor_id,
        before=before,
        after={
            "emailType": email.email_type.value,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
        },
    )
    db.commit()
    db.refresh(email)
    return email


def delete_customer_email(db: Session, *, email: CustomerEmail, actor_id: uuid.UUID) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=email.customer_id,
        tenant_id=email.tenant_id,
        action="email_remove",
        actor_id=actor_id,
        before={
            "emailType": email.email_type.value,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
        },
    )
    db.delete(email)
    db.commit()


def _unset_other_primaries(db: Session, model: type, *, customer_id: uuid.UUID) -> None:
    rows = db.scalars(
        select(model).where(model.customer_id == customer_id, model.is_primary.is_(True))  # type: ignore[attr-defined]
    ).all()
    for row in rows:
        row.is_primary = False


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
    db: Session, *, tenant_id: uuid.UUID, customer_id: uuid.UUID, external_id_row_id: uuid.UUID
) -> CustomerExternalId:
    row = get_or_404(db, CustomerExternalId, external_id_row_id, tenant_id)
    if row.customer_id != customer_id:
        raise NotFoundError(f"CustomerExternalId {external_id_row_id} was not found.")
    return row


def create_customer_external_id(
    db: Session, *, customer: Customer, data: CustomerExternalIdCreate, actor_id: uuid.UUID
) -> CustomerExternalId:
    row = CustomerExternalId(
        tenant_id=customer.tenant_id,
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
            details={"systemName": data.system_name, "externalId": data.external_id},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.tenant_id,
        action="external_id_add",
        actor_id=actor_id,
        after={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.commit()
    db.refresh(row)
    return row


def update_customer_external_id(
    db: Session, *, row: CustomerExternalId, data: CustomerExternalIdUpdate, actor_id: uuid.UUID
) -> CustomerExternalId:
    changes = data.model_dump(exclude_unset=True)
    before = {"systemName": row.system_name, "externalId": row.external_id}

    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_by = actor_id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This system already has an external ID on this customer, or this external ID is already"
            " used under this system for another customer in this dealer.",
            details={"systemName": row.system_name, "externalId": row.external_id},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=row.customer_id,
        tenant_id=row.tenant_id,
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
        tenant_id=row.tenant_id,
        action="external_id_remove",
        actor_id=actor_id,
        before={"systemName": row.system_name, "externalId": row.external_id},
    )
    db.delete(row)
    db.commit()
