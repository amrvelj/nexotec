"""Customer service layer: tenant-scoped CRUD, duplicate-check typeahead,
and merge. PII changes (name/email/phone/address) and lifecycle_status
transitions are audit-logged with before/after (spec: "Every change to PII
... is audit-logged"; lifecycle_status added for the same accountability
reason Dealer/User audit their status fields).
"""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.config import get_settings
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.core.postal_codes import derive_canton
from app.core.tenancy import get_or_404
from app.models.base import utcnow
from app.models.customer import (
    Customer,
    CustomerEmail,
    CustomerExternalId,
    CustomerLifecycleStatus,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerType,
)
from app.models.transaction import Transaction
from app.models.vehicle import VehicleParty
from app.schemas.customer import (
    CustomerCreate,
    CustomerEmailCreate,
    CustomerEmailUpdate,
    CustomerExternalIdCreate,
    CustomerExternalIdUpdate,
    CustomerPhoneCreate,
    CustomerPhoneUpdate,
    CustomerUpdate,
    CustomerVehicleCreate,
    CustomerVehicleUpdate,
)
from app.schemas.validators import normalise_phone
from app.services.audit import record_audit_event
from app.services.vehicle import get_vehicle_or_404

_PII_FIELDS = {
    "salutation",
    "first_name",
    "last_name",
    "birth_date",
    "nationality",
    "company_name",
    "legal_form",
    "tax_id",
    "address_street",
    "address_house_number",
    "address_postal_code",
    "address_locality",
    "address_canton",
    "address_country",
}
# customer_number and language are audited too: the number because it is
# the immutable business key printed on documents, language because sending
# a customer a contract in the wrong language is exactly the kind of change
# someone later disputes.
_AUDITED_FIELDS = _PII_FIELDS | {
    "customer_number",
    "language",
    "lifecycle_status",
    "preferred_channel",
}
# Below this many digits a phone fragment matches most of the table, so it is
# treated as a name search instead.
_MIN_PHONE_SEARCH_DIGITS = 3
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


def _allocate_customer_number(db: Session, tenant_id: uuid.UUID) -> str:
    """Allocate the next `K-000001`-style number for this tenant (D-02).

    Takes a row lock on the tenant's counter so two concurrent creates
    serialise here rather than racing to the same number; the lock is held
    until the surrounding customer-creation transaction commits. On SQLite
    (the fast test lane) `with_for_update` is a no-op, which is fine —
    SQLite serialises writers anyway.

    Numbers are allocated, not derived from a COUNT: a failed transaction
    burns a number and that is deliberate. Gaps are harmless; reuse is not,
    because a reused number would silently point at two different customers
    in printed documents.
    """

    row = db.get(CustomerNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        row = CustomerNumberSequence(tenant_id=tenant_id, next_value=1)
        db.add(row)
        db.flush()
        row = db.get(CustomerNumberSequence, tenant_id, with_for_update=True)

    value = row.next_value
    row.next_value = value + 1
    db.flush()
    return f"K-{value:06d}"


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


def _search_predicate(tenant_id: uuid.UUID, q: str):
    """The FR-01 "one search box" predicate, shared by list and duplicate check.

    Covers company_name and customer_number, which the pre-Phase-B version
    did not: a business customer could not be found by its own name, and no
    search reached the number staff actually quote (D-06). Phone matching
    goes through the normalised column so '079 123 45 67' finds a number
    stored as '+41791234567'.
    """

    pattern = f"%{q}%"
    conditions = [
        Customer.first_name.ilike(pattern),
        Customer.last_name.ilike(pattern),
        Customer.company_name.ilike(pattern),
        Customer.customer_number.ilike(pattern),
        Customer.id.in_(
            select(CustomerEmail.customer_id).where(
                CustomerEmail.tenant_id == tenant_id,
                CustomerEmail.email_address.ilike(pattern),
            )
        ),
    ]
    phone_digits = normalise_phone(q)
    if len(phone_digits) >= _MIN_PHONE_SEARCH_DIGITS:
        conditions.append(
            Customer.id.in_(
                select(CustomerPhone.customer_id).where(
                    CustomerPhone.tenant_id == tenant_id,
                    CustomerPhone.phone_normalised.like(f"%{phone_digits}%"),
                )
            )
        )
    return or_(*conditions)


def list_customers(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    q: str | None,
    lifecycle_status: CustomerLifecycleStatus | None,
    updated_since,
    params: SortPageParams,
    include_merged: bool = False,
) -> tuple[list[Customer], str | None, int, bool]:
    stmt = select(Customer).where(Customer.tenant_id == tenant_id)
    if q:
        stmt = stmt.where(_search_predicate(tenant_id, q))
    if lifecycle_status is not None:
        stmt = stmt.where(Customer.lifecycle_status == lifecycle_status)
    elif not include_merged:
        # A merged record is a tombstone pointing at its survivor. Showing it
        # in normal search results is how staff end up re-opening the record
        # they just merged away, so it is hidden unless explicitly asked for
        # (or explicitly filtered to, via lifecycle_status=merged).
        stmt = stmt.where(Customer.lifecycle_status != CustomerLifecycleStatus.MERGED)
    if updated_since is not None:
        stmt = stmt.where(Customer.updated_at >= updated_since)
    # Counted before pagination is applied (no ORDER BY/LIMIT/cursor yet) —
    # see count_capped for why this can never turn into a full table scan.
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=Customer, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate


def _primary_contact_maps(db: Session, customer_ids):
    """Primary phone/email per customer, in two queries rather than 2N."""

    phones, emails = {}, {}
    if not customer_ids:
        return phones, emails
    for row in db.scalars(select(CustomerPhone).where(CustomerPhone.customer_id.in_(customer_ids))).all():
        if row.is_primary or row.customer_id not in phones:
            phones[row.customer_id] = row.phone_e164
    for row in db.scalars(select(CustomerEmail).where(CustomerEmail.customer_id.in_(customer_ids))).all():
        if row.is_primary or row.customer_id not in emails:
            emails[row.customer_id] = row.email_address
    return phones, emails


def duplicate_check(db: Session, *, tenant_id: uuid.UUID, q: str) -> list[dict[str, Any]]:
    """Advisory typeahead (Swiss addendum Round 2 #5) — never a blocking gate
    on create. A dealership sometimes has a legitimate reason to create a
    near-identical record; FR-09 merge exists for the rest.

    Returns plain dicts rather than Customer rows because a candidate is not
    a customer: it carries a match reason and the primary contact details,
    and it must render for a *business* customer too. The old version typed
    first_name/last_name as required, so a company among the candidates blew
    up serialisation — duplicate detection failed exactly when it found a
    company (D-07).
    """

    candidates = list(
        db.scalars(
            select(Customer)
            .where(
                Customer.tenant_id == tenant_id,
                Customer.lifecycle_status != CustomerLifecycleStatus.MERGED,
                _search_predicate(tenant_id, q),
            )
            .limit(_DUPLICATE_CHECK_LIMIT * 3)
        ).all()
    )
    if not candidates:
        return []

    phones, emails = _primary_contact_maps(db, [c.id for c in candidates])
    needle = q.strip().lower()
    needle_digits = normalise_phone(q)

    exact_email_ids = {
        row.customer_id
        for row in db.scalars(
            select(CustomerEmail).where(
                CustomerEmail.tenant_id == tenant_id,
                func.lower(CustomerEmail.email_address) == needle,
            )
        ).all()
    }
    exact_phone_ids = set()
    if len(needle_digits) >= 7:
        exact_phone_ids = {
            row.customer_id
            for row in db.scalars(
                select(CustomerPhone).where(
                    CustomerPhone.tenant_id == tenant_id,
                    CustomerPhone.phone_normalised == needle_digits,
                )
            ).all()
        }

    results = []
    for customer in candidates:
        is_exact = customer.id in exact_email_ids or customer.id in exact_phone_ids
        results.append(
            {
                "id": customer.id,
                "customer_number": customer.customer_number,
                "customer_type": customer.customer_type,
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "company_name": customer.company_name,
                "primary_phone": phones.get(customer.id),
                "primary_email": emails.get(customer.id),
                "lifecycle_status": customer.lifecycle_status,
                "match": "exact" if is_exact else "similar",
            }
        )

    results.sort(key=lambda r: (r["match"] != "exact", (r["last_name"] or r["company_name"] or "").lower()))
    return results[:_DUPLICATE_CHECK_LIMIT]


def _add_contacts(db: Session, customer: Customer, data: CustomerCreate) -> None:
    """Write the nested phones/emails from a create request.

    Runs inside the caller's transaction, so a customer and its contact
    details commit together or not at all — there is no window in which a
    customer exists while violating its own "at least one contact point"
    invariant (FR-03).

    If the caller marked no entry as primary, the first one wins: every
    customer must have exactly one primary per contact type, and silently
    choosing the first is friendlier than a 422 over something the UI
    should have defaulted.
    """

    for index, phone in enumerate(data.phones):
        db.add(
            CustomerPhone(
                tenant_id=customer.tenant_id,
                customer_id=customer.id,
                phone_type=phone.phone_type,
                phone_e164=phone.phone_e164,
                phone_normalised=normalise_phone(phone.phone_e164),
                is_primary=phone.is_primary or (index == 0 and not any(p.is_primary for p in data.phones)),
                created_by=customer.created_by,
                updated_by=customer.updated_by,
            )
        )
    for index, email in enumerate(data.emails):
        db.add(
            CustomerEmail(
                tenant_id=customer.tenant_id,
                customer_id=customer.id,
                email_type=email.email_type,
                email_address=email.email_address,
                is_primary=email.is_primary or (index == 0 and not any(e.is_primary for e in data.emails)),
                created_by=customer.created_by,
                updated_by=customer.updated_by,
            )
        )


def create_customer(db: Session, *, tenant_id: uuid.UUID, data: CustomerCreate, actor_id: uuid.UUID) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        customer_number=_allocate_customer_number(db, tenant_id),
        customer_type=data.customer_type,
        language=data.language,
        salutation=data.salutation,
        first_name=data.first_name,
        last_name=data.last_name,
        birth_date=data.birth_date,
        nationality=data.nationality,
        company_name=data.company_name,
        legal_form=data.legal_form,
        tax_id=data.tax_id,
        preferred_channel=data.preferred_channel,
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
        # No canton on CustomerAddress (unlike Dealer's) — the client never
        # supplies it, it's derived server-side from the postal code (D-13).
        customer.address_canton = derive_canton(data.address.postal_code, data.address.country)
        customer.address_country = data.address.country

    db.add(customer)
    db.flush()
    _add_contacts(db, customer, data)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # No longer an email-uniqueness failure: the tenant-wide unique
        # constraint on email was dropped in Phase B (D-05), because family
        # members and colleagues legitimately share an address. What can
        # still collide here is the same number/address supplied twice for
        # one customer, or a customer_number race.
        raise ConflictError("Customer contact details conflict with an existing record.") from exc

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
            "address_canton": (
                derive_canton(data.address.postal_code, data.address.country) if data.address else None
            ),
            "address_country": data.address.country if data.address else None,
        }
        for field, value in address_fields.items():
            current = getattr(customer, field)
            if current == value:
                continue
            before[field] = current
            after[field] = value
            setattr(customer, field, value)

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
        raise ConflictError("This change conflicts with an existing record.") from exc
    db.refresh(customer)
    return customer


def _fixup_single_primary(db: Session, model: type, *, customer_id: uuid.UUID) -> None:
    """Collapses a contact-point collection back to exactly one primary
    after re-pointing may have left it with zero (target had none, gained
    some from the duplicate) or several (both sides already had one).
    Oldest row wins when none is marked — same "first one wins" rule
    _add_contacts uses at creation.
    """

    rows = list(
        db.scalars(
            select(model).where(model.customer_id == customer_id).order_by(model.created_at)  # type: ignore[attr-defined]
        ).all()
    )
    primaries = [r for r in rows if r.is_primary]
    if len(primaries) > 1:
        for row in primaries[1:]:
            row.is_primary = False
    elif not primaries and rows:
        rows[0].is_primary = True


def _repoint_vehicle_parties(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    target_keys = {
        (p.vehicle_id, p.role, p.effective_from)
        for p in db.scalars(select(VehicleParty).where(VehicleParty.customer_id == target_id)).all()
    }
    repointed = dropped = 0
    for party in db.scalars(select(VehicleParty).where(VehicleParty.customer_id == duplicate_id)).all():
        key = (party.vehicle_id, party.role, party.effective_from)
        if key in target_keys:
            # Survivor already holds this exact role/period on this vehicle
            # — re-pointing would violate uq_vehicle_party_scope, and the
            # duplicate's copy adds nothing history the survivor doesn't
            # already have.
            db.delete(party)
            dropped += 1
        else:
            party.customer_id = target_id
            target_keys.add(key)
            repointed += 1
    return repointed, dropped


def _repoint_transactions(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> int:
    rows = list(db.scalars(select(Transaction).where(Transaction.customer_id == duplicate_id)).all())
    for txn in rows:
        txn.customer_id = target_id
    return len(rows)


def _repoint_contacts(db: Session, model: type, unique_field: str, *, duplicate_id: uuid.UUID, target_id: uuid.UUID
                       ) -> tuple[int, int]:
    """Shared logic for CustomerPhone/CustomerEmail: re-point onto the
    survivor unless it already has that exact phone number / email address,
    in which case the duplicate's row is dropped as an exact duplicate.
    """

    target_values = {
        getattr(row, unique_field)
        for row in db.scalars(select(model).where(model.customer_id == target_id)).all()  # type: ignore[attr-defined]
    }
    repointed = dropped = 0
    for row in db.scalars(select(model).where(model.customer_id == duplicate_id)).all():  # type: ignore[attr-defined]
        value = getattr(row, unique_field)
        if value in target_values:
            db.delete(row)
            dropped += 1
        else:
            row.customer_id = target_id
            target_values.add(value)
            repointed += 1
    if repointed:
        db.flush()
        _fixup_single_primary(db, model, customer_id=target_id)
    return repointed, dropped


def _repoint_external_ids(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    """system_name is unique per customer (uq_customer_external_id_customer_
    system) — if the survivor already has a link for that system, its own
    linkage wins and the duplicate's is dropped rather than overwritten.
    """

    target_systems = {
        row.system_name
        for row in db.scalars(select(CustomerExternalId).where(CustomerExternalId.customer_id == target_id)).all()
    }
    repointed = dropped = 0
    for row in db.scalars(
        select(CustomerExternalId).where(CustomerExternalId.customer_id == duplicate_id)
    ).all():
        if row.system_name in target_systems:
            db.delete(row)
            dropped += 1
        else:
            row.customer_id = target_id
            target_systems.add(row.system_name)
            repointed += 1
    return repointed, dropped


def merge_customer(
    db: Session, *, customer: Customer, duplicate_of_customer_id: uuid.UUID, actor_id: uuid.UUID
) -> Customer:
    """Merges `customer` (the duplicate) into the survivor (FR-09).

    Re-points vehicle-party rows, transactions, phones, emails and external
    IDs at the survivor in the same transaction as the flag flip — a merge
    that only sets the flag silently orphans the survivor's 360 view from
    everything the duplicate used to carry, which the PRD's own Risks
    section calls "worse than no merge... a correctness bug, not an
    enhancement." Conflicting child rows (the same phone, email, external-id
    system, or vehicle role+period already present on both) are dropped
    from the duplicate rather than erroring — the survivor's copy wins.
    """

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

    parties_repointed, parties_dropped = _repoint_vehicle_parties(
        db, duplicate_id=customer.id, target_id=target.id
    )
    transactions_repointed = _repoint_transactions(db, duplicate_id=customer.id, target_id=target.id)
    phones_repointed, phones_dropped = _repoint_contacts(
        db, CustomerPhone, "phone_e164", duplicate_id=customer.id, target_id=target.id
    )
    emails_repointed, emails_dropped = _repoint_contacts(
        db, CustomerEmail, "email_address", duplicate_id=customer.id, target_id=target.id
    )
    external_ids_repointed, external_ids_dropped = _repoint_external_ids(
        db, duplicate_id=customer.id, target_id=target.id
    )

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
        after={
            "lifecycleStatus": "merged",
            "duplicateOfCustomerId": str(duplicate_of_customer_id),
            "vehiclePartiesRepointed": parties_repointed,
            "vehiclePartiesDropped": parties_dropped,
            "transactionsRepointed": transactions_repointed,
            "phonesRepointed": phones_repointed,
            "phonesDropped": phones_dropped,
            "emailsRepointed": emails_repointed,
            "emailsDropped": emails_dropped,
            "externalIdsRepointed": external_ids_repointed,
            "externalIdsDropped": external_ids_dropped,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Merge conflicts with an existing record on the survivor.") from exc
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
        phone_normalised=normalise_phone(data.phone_e164),
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
    if "phone_e164" in changes:
        phone.phone_normalised = normalise_phone(phone.phone_e164)
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


def _assert_not_last_contact_point(db: Session, customer_id: uuid.UUID, *, removing: str) -> None:
    """FR-03's "at least one contact point" is an invariant of the customer,
    not just of the create request — so deleting the final phone/email is
    rejected. Without this the rule would hold at creation and then quietly
    decay, which is how you end up with customers nobody can reach.
    """

    phones = db.scalar(select(func.count()).select_from(CustomerPhone).where(CustomerPhone.customer_id == customer_id))
    emails = db.scalar(select(func.count()).select_from(CustomerEmail).where(CustomerEmail.customer_id == customer_id))
    if (phones or 0) + (emails or 0) <= 1:
        raise BadRequestError(
            f"Cannot delete the last {removing} — a customer must keep at least one phone number or email address."
        )


def delete_customer_phone(db: Session, *, phone: CustomerPhone, actor_id: uuid.UUID) -> None:
    _assert_not_last_contact_point(db, phone.customer_id, removing="phone number")
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
    _assert_not_last_contact_point(db, email.customer_id, removing="email address")
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


# --- VehicleParty: customer-to-vehicle relationships (Customer PRD D-12,
# FR-10). Vehicle is tenant-agnostic (no tenant_id — a VIN is global, see
# app/models/vehicle.py), so the tenant boundary here is enforced purely by
# resolving `customer` through get_customer_or_404 before any of these run;
# the vehicle_id itself is looked up with no tenant filter, same as every
# other vehicle lookup in the codebase.


def _validate_effective_range(effective_from, effective_to) -> None:
    if effective_to is not None and effective_to <= effective_from:
        raise BadRequestError("effective_to must be after effective_from.")


def list_customer_vehicles(db: Session, *, customer_id: uuid.UUID) -> list[VehicleParty]:
    stmt = (
        select(VehicleParty)
        .options(joinedload(VehicleParty.vehicle))
        .where(VehicleParty.customer_id == customer_id)
        .order_by(VehicleParty.effective_from.desc())
    )
    return list(db.scalars(stmt).all())


def get_customer_vehicle_or_404(db: Session, *, customer_id: uuid.UUID, party_id: uuid.UUID) -> VehicleParty:
    party = db.scalar(
        select(VehicleParty)
        .options(joinedload(VehicleParty.vehicle))
        .where(VehicleParty.id == party_id, VehicleParty.customer_id == customer_id)
    )
    if party is None:
        raise NotFoundError(f"VehicleParty {party_id} was not found.")
    return party


def create_customer_vehicle(
    db: Session, *, customer: Customer, data: CustomerVehicleCreate, actor_id: uuid.UUID
) -> VehicleParty:
    vehicle = get_vehicle_or_404(db, data.vehicle_id)
    effective_from = data.effective_from or utcnow()
    _validate_effective_range(effective_from, data.effective_to)

    party = VehicleParty(
        vehicle_id=vehicle.id,
        customer_id=customer.id,
        role=data.role,
        effective_from=effective_from,
        effective_to=data.effective_to,
    )
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={"vehicleId": str(vehicle.id), "role": data.role.value, "effectiveFrom": effective_from.isoformat()},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.tenant_id,
        action="vehicle_party_add",
        actor_id=actor_id,
        after={
            "vehicleId": str(vehicle.id),
            "role": party.role.value,
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        },
    )
    db.commit()
    db.refresh(party)
    return party


def update_customer_vehicle(
    db: Session, *, party: VehicleParty, data: CustomerVehicleUpdate, actor_id: uuid.UUID, tenant_id: uuid.UUID
) -> VehicleParty:
    changes = data.model_dump(exclude_unset=True)
    before = {
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }

    new_effective_from = changes.get("effective_from", party.effective_from)
    new_effective_to = changes.get("effective_to", party.effective_to)
    _validate_effective_range(new_effective_from, new_effective_to)

    for field, value in changes.items():
        setattr(party, field, value)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={"vehicleId": str(party.vehicle_id), "role": party.role.value},
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=tenant_id,
        action="vehicle_party_update",
        actor_id=actor_id,
        before=before,
        after={
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        },
    )
    db.commit()
    db.refresh(party)
    return party


def delete_customer_vehicle(db: Session, *, party: VehicleParty, actor_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=tenant_id,
        action="vehicle_party_remove",
        actor_id=actor_id,
        before={
            "vehicleId": str(party.vehicle_id),
            "role": party.role.value,
            "effectiveFrom": party.effective_from.isoformat(),
        },
    )
    db.delete(party)
    db.commit()
