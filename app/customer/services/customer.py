"""Customer service layer: GROUP-scoped CRUD (WP-3 PR-2, ADR-014 — moved
from tenant-scoped), duplicate-check typeahead, and merge, all operating
group-wide. PII changes (name/email/phone/address) and lifecycle_status
transitions are audit-logged with before/after (spec: "Every change to PII
... is audit-logged"; lifecycle_status added for the same accountability
reason Dealership/User audit their status fields).

`app.core.audit.record_audit_event`/`app.core.outbox.OutboxEvent` keep their
own generic `tenant_id` keyword — that's a cross-cutting parameter name
shared by every context, not something this one PR renames everywhere — but
every call site here now passes `customer.group_id` as its value, since
that's Customer's real scoping key.
"""

import datetime as dt
import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session, load_only

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.core.postal_codes import derive_canton
from app.core.redact import REDACTED_PLACEHOLDER, is_secret_field
from app.core.validators import normalise_phone
from app.customer.models.customer import (
    AddressType,
    ConsentScope,
    Customer,
    CustomerAddress,
    CustomerEmail,
    CustomerExternalId,
    CustomerLifecycleStatus,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerTag,
    CustomerType,
    EmailType,
    Language,
    PhoneType,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole, VehiclePartyVehicleLabel
from app.customer.schemas.customer import (
    CustomerAddressCreate,
    CustomerAddressRead,
    CustomerAddressUpdate,
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
    OtherVehiclePartySummary,
)
from app.platform.public import (
    get_active_reference_value_codes,
    get_active_user_acting_in,
    get_group_display_labels,
    list_active_users_acting_in,
)
from app.vehicle.public import VehicleSummary, get_vehicle_mdm_or_404, get_vehicle_summaries

logger = logging.getLogger("app.customer")

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
    # FR-17 (KAN-50). `notes` is PII by default — a note field not treated
    # as PII is where the compliance problem hides; it is the attach point
    # for the revDSG export and FR-14 anonymisation once those are built.
    # `title` and `gender` are personal identity facts; `iban` is a
    # financial identifier.
    "title",
    "gender",
    "notes",
    "iban",
}
# customer_number and language are audited too: the number because it is
# the immutable business key printed on documents, language because sending
# a customer a contract in the wrong language is exactly the kind of change
# someone later disputes. The FR-17 commercial-standing and relationship
# fields (KAN-50) are audited but are not all PII.
_AUDITED_FIELDS = _PII_FIELDS | {
    "customer_number",
    "language",
    "lifecycle_status",
    "preferred_channel",
    "website",
    "newsletter",
    "payment_terms",
    "credit_limit",
    "vat_registered",
    "vat_status_confirmed",
    "advisor_id",
    "customer_since",
    "next_follow_up",
    "dealership_id",
}
# tags live in a child table (no `customer.tags` attribute) — audited
# explicitly by create_customer / update_customer, not via the generic
# _AUDITED_FIELDS comprehension.
# Below this many digits a phone fragment matches most of the table, so it is
# treated as a name search instead.
_MIN_PHONE_SEARCH_DIGITS = 3
_TERMINAL_LIFECYCLE_STATUSES = {CustomerLifecycleStatus.MERGED}
_DUPLICATE_CHECK_LIMIT = 10
# Outbox producer name for every event this service publishes (WP-1, ADR-006).
_EVENT_PRODUCER = "customer"
# is_primary is scoped to (customer_id, type) since WP-3 PR-5 (ADR-067), not
# to the whole customer — each contact-channel model's own type column, so
# the shared primary-fixup/repoint logic below can stay generic over all
# three tables instead of three near-identical copies.
_CONTACT_TYPE_COLUMN: dict[type, str] = {
    CustomerPhone: "phone_type",
    CustomerEmail: "email_type",
    CustomerAddress: "address_type",
}


def _default_primary_flags(items: list[Any], type_of) -> list[bool]:
    """Per contact-type-group, not per customer (ADR-067): if the caller
    marked no entry of a given type as primary, the first entry of that type
    in the list wins — same "first one wins" rule as before, now applied
    independently within each type instead of once across the whole list.
    """

    first_index_by_type: dict[Any, int] = {}
    has_primary_by_type: dict[Any, bool] = {}
    for index, item in enumerate(items):
        type_value = type_of(item)
        first_index_by_type.setdefault(type_value, index)
        has_primary_by_type[type_value] = has_primary_by_type.get(type_value, False) or item.is_primary
    return [
        not has_primary_by_type[type_of(item)] and index == first_index_by_type[type_of(item)]
        for index, item in enumerate(items)
    ]


def _group_scoped_or_404(db: Session, model: type, entity_id: uuid.UUID, group_id: uuid.UUID) -> Any:
    """app.core.tenancy.get_or_404's shape, but filtering on group_id — this
    context's own scoping column since WP-3 PR-2 (ADR-014), not the
    tenant_id every other context still uses. Kept local rather than added
    to app.core.tenancy: that module is one explicit convention (tenant_id)
    shared by every other context, and Customer is deliberately the one
    exception, not a second convention to generalize the shared helper for.
    """

    obj = (
        db.query(model)
        .filter(model.id == entity_id, model.group_id == group_id)  # type: ignore[attr-defined]
        .one_or_none()
    )
    if obj is None:
        raise NotFoundError(f"{model.__name__} {entity_id} was not found.")
    return obj


def _plain(value: Any) -> Any:
    # A date/datetime has no ``.value`` and is not JSON-serialisable by the
    # engine's default ``json.dumps`` — it would reach the audit writer raw
    # and raise ``TypeError``. ``.isoformat()`` is already the convention for
    # every hand-written audit payload in this file (the vehicle-party events
    # below), so the generic path agrees with them rather than inventing a
    # second one. ``dt.datetime`` is a ``dt.date`` subclass, so this covers
    # both. Checked before the ``.value`` branch because neither type has it.
    if isinstance(value, dt.date):
        return value.isoformat()
    # Decimal (credit_limit) and UUID (advisor_id, dealership_id) are not
    # JSON-serialisable by the engine's default json.dumps either — same
    # reasoning as the date branch above.
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    return value.value if hasattr(value, "value") else value


def _customer_label_payload(customer: Customer) -> dict[str, Any]:
    """The subset of a customer another context needs to refresh a
    denormalised label — never tax_id, never the rest of the PII surface.
    """

    return {
        "customerId": str(customer.id),
        "customerNumber": customer.customer_number,
        "customerType": customer.customer_type.value,
        "firstName": customer.first_name,
        "lastName": customer.last_name,
        "companyName": customer.company_name,
    }


def _redact(field: str, value: Any) -> Any:
    if is_secret_field(field) and value is not None:
        return REDACTED_PLACEHOLDER
    return _plain(value)


_INDIVIDUAL_ONLY_FIELDS = {"first_name", "last_name", "birth_date", "nationality", "title", "gender"}
_BUSINESS_ONLY_FIELDS = {"company_name", "legal_form", "tax_id"}

# KAN-32: `nationality` and every `address_country` are ISO 3166-1 alpha-2
# codes drawn from the platform `country` reference list. A pydantic schema
# cannot reach the DB, so membership is enforced here — the same place
# `app.vehicle.services.vehicle` validates its reference fields.
_COUNTRY_LIST_CODE = "country"


def _validate_country_codes(db: Session, values: dict[str, str | None]) -> None:
    """Reject any non-null value in ``values`` that is not an active code in
    the ``country`` reference list. ``values`` maps a human field label
    (used verbatim in the error) to the submitted code.

    One query for the whole call, regardless of how many addresses a nested
    create carries. A missing list is a deployment fault (the platform-branch
    seed migration has not run), surfaced as a 500 rather than a 422/404 on
    ``POST /customers`` — see ``get_active_reference_value_codes``.
    """

    submitted = {label: code for label, code in values.items() if code is not None}
    if not submitted:
        return

    valid = get_active_reference_value_codes(db, _COUNTRY_LIST_CODE)
    if valid is None:
        raise RuntimeError(
            "The 'country' reference list is not seeded. Run `alembic upgrade heads` "
            "(migration d7b1f4e02a96) on this deployment — customer nationality and "
            "address country cannot be validated without it."
        )

    invalid = {label: code for label, code in submitted.items() if code not in valid}
    if invalid:
        rendered = ", ".join(f"{label}={code!r}" for label, code in sorted(invalid.items()))
        raise UnprocessableEntityError(
            f"Not a valid country code: {rendered}. Expected an ISO 3166-1 alpha-2 code "
            "that is present and active in the 'country' reference list (for example 'CH', "
            "'DE', 'FR').",
            details={"invalid": invalid},
        )


def _resolve_advisor_label(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Resolve a User id to its `First Last` label for the P-2 denormalised
    `advisor_label` column. Scoped to the acting dealership: "the advisor" is
    someone who may own an open record there — Sales' rule for a responsible
    seller, platform's one predicate (`get_active_user_acting_in`; KAN-410,
    Anto 2026-10-23): homed in the Betrieb or an active member of it (a
    sister-Betrieb colleague who works here advises the customers they take
    on here), and active or invited. A suspended or deactivated user advises
    no customer, since the deactivation step hands a leaver's customers over
    (FR-A-37, D-A-35) and a write must not put them back.
    """

    user = get_active_user_acting_in(db, dealership_id=dealership_id, user_id=user_id)
    if user is None:
        raise UnprocessableEntityError(
            "advisorId is not an active user of your dealership.", details={"advisorId": str(user_id)}
        )
    return f"{user.first_name} {user.last_name}".strip()


def list_advisor_options(db: Session, *, dealership_id: uuid.UUID) -> list[dict[str, Any]]:
    """Everyone `_resolve_advisor_label` admits for the acting dealership —
    its active and invited users and its active members (KAN-410) — as
    advisor-picker options (KAN-50). Same `First Last` label that lands in
    Customer.advisor_label."""

    return [
        {"id": user.id, "label": f"{user.first_name} {user.last_name}".strip()}
        for user in list_active_users_acting_in(db, dealership_id=dealership_id)
    ]


def _apply_advisor(customer: Customer, *, advisor_id: uuid.UUID | None, label: str | None) -> None:
    customer.advisor_id = advisor_id
    customer.advisor_label = label
    customer.advisor_label_refreshed_at = utcnow() if advisor_id is not None else None


def _replace_customer_tags(db: Session, customer: Customer, tags: list[str], *, actor_id: uuid.UUID) -> None:
    """Reconcile customer_tag rows to exactly `tags` (already normalised by
    the schema): insert the missing, delete the absent. One local
    transaction; the caller commits.
    """

    existing = {row.tag: row for row in db.scalars(select(CustomerTag).where(CustomerTag.customer_id == customer.id)).all()}
    wanted = set(tags)
    for tag in wanted - existing.keys():
        db.add(
            CustomerTag(
                group_id=customer.group_id,
                customer_id=customer.id,
                tag=tag,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    for tag, row in existing.items():
        if tag not in wanted:
            db.delete(row)


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint a Postgres IntegrityError violated (None on
    SQLite, whose driver does not report it)."""

    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _customer_tags(db: Session, customer_id: uuid.UUID) -> list[str]:
    return sorted(db.scalars(select(CustomerTag.tag).where(CustomerTag.customer_id == customer_id)).all())


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


def _allocate_customer_number(db: Session, group_id: uuid.UUID) -> str:
    """Allocate the next `K-000001`-style number for this group (D-02, moved
    from per-dealership to per-group in WP-3 PR-2).

    Takes a row lock on the group's counter so two concurrent creates
    serialise here rather than racing to the same number; the lock is held
    until the surrounding customer-creation transaction commits. On SQLite
    (the fast test lane) `with_for_update` is a no-op, which is fine —
    SQLite serialises writers anyway.

    Numbers are allocated, not derived from a COUNT. A failed transaction
    rolls back its increment with everything else, so the next caller is
    re-issued that number (G-71) — it never reached a committed customer.
    What must never happen is reuse of a committed number, because it
    would silently point at two different customers in printed documents.
    """

    row = db.get(CustomerNumberSequence, group_id, with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(CustomerNumberSequence(group_id=group_id, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(CustomerNumberSequence, group_id, with_for_update=True)
        assert row is not None, "CustomerNumberSequence row missing after its first-use INSERT or the concurrent winner's"

    value = row.next_value
    row.next_value = value + 1
    db.flush()
    return f"K-{value:06d}"


def get_customer_or_404(db: Session, group_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    return _group_scoped_or_404(db, Customer, customer_id, group_id)


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


def _search_predicate(group_id: uuid.UUID, q: str):
    """The FR-01 "one search box" predicate, shared by list and duplicate
    check. Group-wide since WP-3 PR-2 (ADR-014) — search operates across
    every dealership in the group, not just one.

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
                CustomerEmail.group_id == group_id,
                CustomerEmail.email_address.ilike(pattern),
            )
        ),
    ]
    phone_digits = normalise_phone(q)
    if len(phone_digits) >= _MIN_PHONE_SEARCH_DIGITS:
        conditions.append(
            Customer.id.in_(
                select(CustomerPhone.customer_id).where(
                    CustomerPhone.group_id == group_id,
                    CustomerPhone.phone_normalised.like(f"%{phone_digits}%"),
                )
            )
        )
    return or_(*conditions)


def list_customers(
    db: Session,
    *,
    group_id: uuid.UUID,
    q: str | None,
    lifecycle_status: CustomerLifecycleStatus | None,
    customer_type: CustomerType | None = None,
    language: Language | None = None,
    canton: str | None = None,
    updated_since,
    params: SortPageParams,
    include_merged: bool = False,
    advisor_user_id: uuid.UUID | None = None,
    unassigned: bool = False,
) -> tuple[list[Customer], str | None, int, bool]:
    """`advisor_user_id` narrows to one advisor's customers; `unassigned` to
    those with none — the queue FR-A-37 names, which a leaver's hand-over
    to nobody fills (KAN-200). Both together is an empty page."""

    stmt = select(Customer).where(Customer.group_id == group_id)
    if advisor_user_id is not None:
        stmt = stmt.where(Customer.advisor_id == advisor_user_id)
    if unassigned:
        stmt = stmt.where(Customer.advisor_id.is_(None))
    if q:
        stmt = stmt.where(_search_predicate(group_id, q))
    if lifecycle_status is not None:
        stmt = stmt.where(Customer.lifecycle_status == lifecycle_status)
    elif not include_merged:
        # A merged record is a tombstone pointing at its survivor. Showing it
        # in normal search results is how staff end up re-opening the record
        # they just merged away, so it is hidden unless explicitly asked for
        # (or explicitly filtered to, via lifecycle_status=merged).
        stmt = stmt.where(Customer.lifecycle_status != CustomerLifecycleStatus.MERGED)
    if customer_type is not None:
        stmt = stmt.where(Customer.customer_type == customer_type)
    if language is not None:
        stmt = stmt.where(Customer.language == language)
    if canton is not None:
        # ADR-067 (WP-3 PR-5): canton is a fact of the primary domicile
        # CustomerAddress row now, not the (frozen, read-only-mirror)
        # Customer.address_canton flat column. NOTE: this predicate is a
        # raw is_primary match — it deliberately does NOT apply the
        # `_is_usable_row` filter or the oldest-usable fallback that the
        # `address` projection (and `has_usable_domicile_address`) use, so
        # it can diverge for legacy / bulk-imported rows. Aligning it needs
        # the fallback replicated SQL-side — tracked as its own ticket.
        stmt = stmt.where(
            Customer.id.in_(
                select(CustomerAddress.customer_id).where(
                    CustomerAddress.group_id == group_id,
                    CustomerAddress.address_type == AddressType.DOMICILE,
                    CustomerAddress.is_primary.is_(True),
                    CustomerAddress.address_canton == canton,
                )
            )
        )
    if updated_since is not None:
        stmt = stmt.where(Customer.updated_at >= updated_since)
    # Counted before pagination is applied (no ORDER BY/LIMIT/cursor yet) —
    # see count_capped for why this can never turn into a full table scan.
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=Customer, params=params)
    try:
        rows = list(db.scalars(stmt).all())
    except LookupError:
        # A stored column value that no longer matches any current Python
        # enum member (KAN-60: a legacy preferred_channel value a rename
        # migration missed) fails during SQLAlchemy's bulk row hydration —
        # before any per-row Python code runs, so a normal try/except around
        # row-by-row processing can't isolate it; ALL rows in the page fail
        # together, not just the offending one. Recover by fetching just
        # this page's ids (a bare uuid column, which can never fail to
        # decode) and re-hydrating each customer individually, skipping —
        # and logging — whichever single row still doesn't decode. One
        # corrupt customer must never be able to take the whole list down
        # for every other customer at the same dealer.
        db.rollback()
        id_stmt = stmt.with_only_columns(Customer.id)
        ids = list(db.scalars(id_stmt).all())
        rows = []
        for customer_id in ids:
            try:
                customer = db.get(Customer, customer_id)
            except LookupError:
                db.rollback()
                logger.error(
                    "customer %s has a column value that no longer decodes against its enum — "
                    "omitted from this list page rather than failing the whole request",
                    customer_id,
                )
                continue
            if customer is not None:
                rows.append(customer)
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate


def _primary_contact_maps(db: Session, customer_ids):
    """Primary phone/email per customer, in two queries rather than 2N."""

    phones: dict[uuid.UUID, str] = {}
    emails: dict[uuid.UUID, str] = {}
    if not customer_ids:
        return phones, emails
    for phone_row in db.scalars(select(CustomerPhone).where(CustomerPhone.customer_id.in_(customer_ids))).all():
        if phone_row.is_primary or phone_row.customer_id not in phones:
            phones[phone_row.customer_id] = phone_row.phone_e164
    for email_row in db.scalars(select(CustomerEmail).where(CustomerEmail.customer_id.in_(customer_ids))).all():
        if email_row.is_primary or email_row.customer_id not in emails:
            emails[email_row.customer_id] = email_row.email_address
    return phones, emails


def duplicate_check(db: Session, *, group_id: uuid.UUID, q: str) -> list[dict[str, Any]]:
    """Advisory typeahead (Swiss addendum Round 2 #5) — never a blocking gate
    on create. A dealership sometimes has a legitimate reason to create a
    near-identical record; FR-09 merge exists for the rest. Group-wide since
    WP-3 PR-2 (ADR-014, PRD-Customers FR-04).

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
                Customer.group_id == group_id,
                Customer.lifecycle_status != CustomerLifecycleStatus.MERGED,
                _search_predicate(group_id, q),
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
                CustomerEmail.group_id == group_id,
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
                    CustomerPhone.group_id == group_id,
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
    """Write the nested phones/emails/addresses from a create request.

    Runs inside the caller's transaction, so a customer and its contact
    details commit together or not at all — there is no window in which a
    customer exists while violating its own "at least one contact point"
    invariant (FR-03).

    is_primary defaulting is per (type) within each list — see
    _default_primary_flags — since ADR-067 scopes "exactly one primary" to
    the type-group, not the whole customer.
    """

    for phone, is_default_primary in zip(data.phones, _default_primary_flags(data.phones, lambda p: p.phone_type)):
        db.add(
            CustomerPhone(
                group_id=customer.group_id,
                customer_id=customer.id,
                phone_type=phone.phone_type,
                label=phone.label,
                phone_e164=phone.phone_e164,
                phone_normalised=normalise_phone(phone.phone_e164),
                is_primary=phone.is_primary or is_default_primary,
                created_by=customer.created_by,
                updated_by=customer.updated_by,
                **_consent_create_kwargs(phone),
            )
        )
    for email, is_default_primary in zip(data.emails, _default_primary_flags(data.emails, lambda e: e.email_type)):
        db.add(
            CustomerEmail(
                group_id=customer.group_id,
                customer_id=customer.id,
                email_type=email.email_type,
                label=email.label,
                email_address=email.email_address,
                is_primary=email.is_primary or is_default_primary,
                created_by=customer.created_by,
                updated_by=customer.updated_by,
                **_consent_create_kwargs(email),
            )
        )
    for address, is_default_primary in zip(
        data.addresses, _default_primary_flags(data.addresses, lambda a: a.address_type)
    ):
        db.add(
            CustomerAddress(
                group_id=customer.group_id,
                customer_id=customer.id,
                address_type=address.address_type,
                label=address.label,
                address_street=address.address_street,
                address_line2=address.address_line2,
                address_house_number=address.address_house_number,
                address_postal_code=address.address_postal_code,
                address_locality=address.address_locality,
                address_canton=derive_canton(address.address_postal_code, address.address_country),
                address_country=address.address_country,
                is_primary=address.is_primary or is_default_primary,
                created_by=customer.created_by,
                updated_by=customer.updated_by,
                **_consent_create_kwargs(address),
            )
        )


def create_customer(
    db: Session,
    *,
    group_id: uuid.UUID,
    data: CustomerCreate,
    actor_id: uuid.UUID,
    dealership_id: uuid.UUID,
) -> Customer:
    _validate_country_codes(
        db,
        {
            "nationality": data.nationality,
            **{
                f"addresses[{i}].addressCountry": address.address_country
                for i, address in enumerate(data.addresses)
            },
        },
    )
    customer = Customer(
        group_id=group_id,
        customer_number=_allocate_customer_number(db, group_id),
        customer_type=data.customer_type,
        language=data.language,
        salutation=data.salutation,
        first_name=data.first_name,
        last_name=data.last_name,
        birth_date=data.birth_date,
        nationality=data.nationality,
        title=data.title,
        gender=data.gender,
        company_name=data.company_name,
        legal_form=data.legal_form,
        tax_id=data.tax_id,
        preferred_channel=data.preferred_channel,
        lifecycle_status=data.lifecycle_status,
        source=data.source,
        source_ref=data.source_ref,
        marketing_consent=data.marketing_consent,
        website=data.website,
        newsletter=data.newsletter,
        payment_terms=data.payment_terms,
        credit_limit=data.credit_limit,
        iban=data.iban,
        vat_registered=data.vat_registered,
        vat_status_confirmed=data.vat_status_confirmed,
        customer_since=data.customer_since,
        next_follow_up=data.next_follow_up,
        notes=data.notes,
        # Provenance only — never a read/write scope (ADR-014). Set once.
        dealership_id=dealership_id,
        created_by=actor_id,
        updated_by=actor_id,
    )

    # advisorId defaults to the acting user on create (D-24). A bad
    # explicit id is rejected; a default that cannot be resolved (a
    # back-office / migration actor who is not a dealership user) leaves the
    # customer unassigned rather than failing the whole create.
    advisor_id = data.advisor_id if data.advisor_id is not None else actor_id
    if advisor_id is not None:
        try:
            _apply_advisor(
                customer,
                advisor_id=advisor_id,
                label=_resolve_advisor_label(db, dealership_id=dealership_id, user_id=advisor_id),
            )
        except UnprocessableEntityError:
            if data.advisor_id is not None:
                raise
            _apply_advisor(customer, advisor_id=None, label=None)

    db.add(customer)
    db.flush()
    _add_contacts(db, customer, data)
    _replace_customer_tags(db, customer, data.tags, actor_id=actor_id)
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

    after = {field: _redact(field, getattr(customer, field)) for field in _AUDITED_FIELDS}
    after["tags"] = sorted(data.tags)
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=group_id,
        action="create",
        actor_id=actor_id,
        after=after,
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.created",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="customer",
            aggregate_id=customer.id,
            payload=_customer_label_payload(customer),
        ),
    )
    db.commit()
    db.refresh(customer)
    return customer


def update_customer(
    db: Session,
    *,
    customer: Customer,
    data: CustomerUpdate,
    actor_id: uuid.UUID,
    dealership_id: uuid.UUID,
) -> Customer:
    if customer.lifecycle_status in _TERMINAL_LIFECYCLE_STATUSES:
        raise ConflictError(
            f"Customer lifecycle_status '{customer.lifecycle_status.value}' is terminal and cannot be changed"
            " via PATCH.",
            details={"currentLifecycleStatus": customer.lifecycle_status.value},
        )

    changes = data.model_dump(exclude_unset=True)
    _validate_customer_type_fields_on_update(customer, changes)
    if "nationality" in changes:
        _validate_country_codes(db, {"nationality": changes["nationality"]})

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    # advisor_id is handled out of the generic loop — it drives two more
    # denormalised columns and (D-24) is never re-defaulted on update: an
    # explicit id resolves a fresh label, an explicit null clears all three.
    if "advisor_id" in changes:
        new_advisor_id = changes.pop("advisor_id")
        if customer.advisor_id != new_advisor_id:
            before["advisor_id"] = _redact("advisor_id", customer.advisor_id)
            after["advisor_id"] = _redact("advisor_id", new_advisor_id)
            if new_advisor_id is None:
                _apply_advisor(customer, advisor_id=None, label=None)
            else:
                _apply_advisor(
                    customer,
                    advisor_id=new_advisor_id,
                    label=_resolve_advisor_label(db, dealership_id=dealership_id, user_id=new_advisor_id),
                )

    # tags live in a child table — full-replace against the normalised set.
    if "tags" in changes:
        new_tags = sorted(changes.pop("tags"))
        current_tags = _customer_tags(db, customer.id)
        if new_tags != current_tags:
            before["tags"] = current_tags
            after["tags"] = new_tags
            _replace_customer_tags(db, customer, new_tags, actor_id=actor_id)

    for field, value in changes.items():
        current = getattr(customer, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _redact(field, current)
            after[field] = _redact(field, value)
        setattr(customer, field, value)

    customer.updated_by = actor_id
    customer.version += 1

    # KAN-390: the customer row and its new tag rows reach the database here,
    # inside the guard — not at the audit row's flush below, outside it. A
    # merge into this customer (KAN-372 holds it FOR SHARE) can move a
    # duplicate's tag of the same name onto it while this PATCH waits; the
    # insert then hits uq_customer_tag_customer_id_tag, a conflict (409), not
    # a 500. Tags are this function's only child rows. The PATCH waits before
    # inserting any tag because the flush writes `customer` before
    # `customer_tag` — SQLAlchemy orders the two unrelated mappers by name,
    # not by a relationship — so it never holds a tag row the merge needs.
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _violated_constraint(exc) != "uq_customer_tag_customer_id_tag":
            raise ConflictError("This change conflicts with an existing record.") from exc
        raise ConflictError(
            "A tag in this change is already on the customer: it changed in the meantime (a customer may have"
            " been merged into it). Reload it and try again.",
            details={"reason": "customer_tag_exists"},
        ) from exc

    if before or after:
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=customer.group_id,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )
        publish_event(
            db,
            OutboxEvent(
                event_type="customer.updated",
                tenant_id=customer.group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="customer",
                aggregate_id=customer.id,
                payload=_customer_label_payload(customer),
            ),
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This change conflicts with an existing record.") from exc
    db.refresh(customer)
    return customer


def _is_usable_row(row: Any) -> bool:
    """A contact-channel row is usable when it is neither closed (valid_to
    set) nor flagged do_not_use — the same definition FR-03 and the six
    projections use.
    """

    return row.valid_to is None and not row.do_not_use


def has_usable_domicile_address(db: Session, *, customer_id: uuid.UUID) -> bool:
    """Whether the customer has at least one usable `domicile` address row
    — `valid_to` unset (a future-dated close counts as closed) and not
    `do_not_use`, via the shared `_is_usable_row`.

    This is exactly `CustomerRead.address is not None`: the `address`
    projection resolves the primary-flagged usable domicile row, and
    `_primary_of_type` falls back to the oldest usable domicile row when
    none is flagged — so both this predicate and the projection reduce to
    "≥ 1 usable domicile row exists". Keep in step with
    `compute_customer_projections_batch`, not with `_fixup_single_primary`
    (which the equivalence does not depend on).

    A `billing` (or any non-domicile) address does not satisfy this — D-20,
    ruled 2026-09-07: the Kaufvertrag identifies the buyer by domicile, and
    the billing-address question belongs to WP-9 invoicing.

    Exposed through `customer.public` for Sales' contract-confirmation gate
    (D-20 / KAN-55): a contract cannot be confirmed for a customer whose
    address the dealership does not have. An offer is never blocked.
    """

    rows = db.scalars(
        select(CustomerAddress).where(
            CustomerAddress.customer_id == customer_id,
            CustomerAddress.address_type == AddressType.DOMICILE,
        )
    ).all()
    return any(_is_usable_row(row) for row in rows)


def _fixup_single_primary(db: Session, model: type, *, customer_id: uuid.UUID, type_value: Any) -> None:
    """Collapses one (customer, type) contact-point group back to exactly
    one USABLE primary.

    Called from two places: the merge path (a re-point may leave a
    type-group with zero primaries — target had none of this type and
    gained some from the duplicate — or several — both sides already had
    one) and the write path (closing / flagging / deleting the primary,
    KAN-46). Both want the same end state, so both go through here rather
    than through a second near-identical helper.

    The rule (PRD-Customers FR-23 §2, the prototype's `NX.chPrimary`):

    - a closed or do_not_use row is never a valid primary — demote any that
      is still flagged (a merge can repoint a dead row that was primary in
      its own group; closing a primary leaves it flagged until we get here);
    - among the USABLE rows of the type, keep exactly one primary: the
      oldest by created_at when none or several are flagged;
    - a type-group with no usable row left elects nothing, and the
      projection is null. That is correct — forcing a dead row to be
      primary would put it back on documents, the exact failure do_not_use
      exists to prevent.

    Scoped to type_value, not the whole customer, since ADR-067 (WP-3 PR-5)
    — a customer may legitimately have a primary mobile AND a primary work
    phone at once.
    """

    type_column = _CONTACT_TYPE_COLUMN[model]
    rows: list[Any] = list(
        db.scalars(
            select(model)
            .where(
                model.customer_id == customer_id,  # type: ignore[attr-defined]
                getattr(model, type_column) == type_value,
            )
            .order_by(model.created_at)  # type: ignore[attr-defined]
        ).all()
    )
    for row in rows:
        if row.is_primary and not _is_usable_row(row):
            row.is_primary = False

    usable = [r for r in rows if _is_usable_row(r)]
    primaries = [r for r in usable if r.is_primary]
    if len(primaries) > 1:
        for row in primaries[1:]:
            row.is_primary = False
    elif not primaries and usable:
        usable[0].is_primary = True


def _lock_merge_customers(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """KAN-372: a merge locks both customer rows — the duplicate FOR NO KEY
    UPDATE (KAN-365), the survivor FOR SHARE — and decides on their committed
    status under those locks. The survivor's status was read without a lock,
    and re-pointing rows onto it takes only the foreign key's KEY SHARE, which
    does not wait for the survivor's own merge: so a merge X→D could commit
    while D→T was in flight and leave X's rows, and X's
    `duplicateOfCustomerId`, on a merged customer. FOR SHARE waits for that
    merge's FOR NO KEY UPDATE; the re-read then finds D merged (409
    `merge_target_merged`). The duplicate is re-read too, so a second merge of
    the same customer that waited for the first is refused
    (`customer_already_merged`) instead of overwriting its survivor.

    Lock order: every merge takes its advisory locks first (KAN-356, sorted),
    then these two rows in id order. Two merges in opposite directions (A→B,
    B→A) therefore ask for the two rows in the same order and the second
    waits; without the order each would hold its duplicate and wait for the
    other's. Allocation and a reopen take one customer row FOR SHARE after
    their advisory lock, compatible with a merge's FOR SHARE on its survivor
    and waiting, as KAN-365 intends, for its FOR NO KEY UPDATE on the
    duplicate."""

    for customer_id in sorted((duplicate_id, target_id)):
        stmt = select(Customer.id).where(Customer.id == customer_id)
        if customer_id == duplicate_id:
            db.execute(stmt.with_for_update(key_share=True))
        else:
            db.execute(stmt.with_for_update(read=True))
    status: dict[uuid.UUID, CustomerLifecycleStatus] = {
        row.id: row.lifecycle_status
        for row in db.execute(
            select(Customer.id, Customer.lifecycle_status).where(Customer.id.in_((duplicate_id, target_id)))
        )
    }
    if status[target_id] == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Cannot merge into a customer that has itself been merged.",
            details={"reason": "merge_target_merged", "duplicateOfCustomerId": str(target_id)},
        )
    if status[duplicate_id] == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Customer has already been merged.",
            details={"reason": "customer_already_merged", "currentLifecycleStatus": "merged"},
        )


def _repoint_vehicle_parties(
    db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID, actor_id: uuid.UUID, group_id: uuid.UUID
) -> tuple[int, list[uuid.UUID]]:
    """Re-points the duplicate's party rows at the survivor; returns how
    many, and the ids of the rows it KEPT instead.

    KAN-177 (ADR-064: party history is never deleted; Anto's ruling,
    2026-10-12, option A). A row whose (vehicle, role, effective_from) the
    survivor already holds cannot be re-pointed — uq_vehicle_party_scope is
    (vehicle, customer, role, effective_from), so the survivor's own row
    would collide — and before KAN-177 it was hard-deleted, unaudited. Now
    it stays on the duplicate's customer_id (the one place it does not
    collide), closed at its own start, and marked with the survivor it was
    merged into (`merged_into_customer_id`). The close goes through
    _close_vehicle_party, so it is audited (`vehicle_party_remove`, with
    `mergedIntoCustomerId`) and published (`customer.vehicle_party.unlinked`)
    like every other close; the merge's own audit event lists the ids.

    A copy that was already closed in the past keeps its own effective_to —
    that is recorded history, its unlink already a published fact — and only
    takes the mark, with no second close, audit row or event: the merge's own
    audit event lists its id. One that is open, or closed at a future date,
    is closed at its start so it never reads as a current party again. The
    survivor's row is not touched.

    KAN-356: every allocation and reopen serialises on the (vehicle, role,
    group) advisory lock, and the merge writes the same rows, so it takes that
    lock for every (vehicle, role) the duplicate has a row in — in sorted
    order, so two merges never deadlock each other — before it reads a row,
    then locks the rows it writes (advisory, then row: KAN-148's order). A
    row an allocation added for the duplicate while the merge waited can name
    a pair not locked yet, so the pairs are read again until every one is
    held. Such a late pair is out of the sorted order, so the merge never
    waits for it: it tries the lock, and if another writer holds it the merge
    is refused (409 `merge_vehicle_party_busy`, nothing written) and can
    simply be sent again.
    """

    held: set[tuple[uuid.UUID, VehiclePartyRole]] = set()
    duplicate_locked = False
    while True:
        pairs = {
            (vehicle_id, role)
            for vehicle_id, role in db.execute(
                select(VehicleParty.vehicle_id, VehicleParty.role)
                .where(VehicleParty.customer_id == duplicate_id)
                .distinct()
            ).all()
        }
        missing = sorted(pairs - held, key=lambda pair: (str(pair[0]), pair[1].value))
        if not missing:
            if duplicate_locked:
                break
            # KAN-365: an allocation to the duplicate reads its customer row
            # FOR SHARE under its own advisory lock before it inserts. Once
            # this lock is granted, every such allocation has either committed
            # (its pair is read again below) or waits for the merge and then
            # finds the customer merged.
            _lock_merge_customers(db, duplicate_id=duplicate_id, target_id=target_id)
            duplicate_locked = True
            continue
        late = bool(held) or duplicate_locked
        for vehicle_id, role in missing:
            if not late:
                _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
            elif not _try_lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id):
                raise ConflictError(
                    "A vehicle of this customer is being changed right now; send the merge again.",
                    details={"reason": "merge_vehicle_party_busy", "vehicleId": str(vehicle_id), "role": role.value},
                )
            held.add((vehicle_id, role))

    target_keys = {
        (p.vehicle_id, p.role, p.effective_from)
        for p in db.scalars(select(VehicleParty).where(VehicleParty.customer_id == target_id)).all()
    }
    repointed = 0
    kept_closed: list[uuid.UUID] = []
    duplicate_rows = (
        select(VehicleParty)
        .where(VehicleParty.customer_id == duplicate_id)
        .order_by(VehicleParty.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for party in db.scalars(duplicate_rows).all():
        key = (party.vehicle_id, party.role, party.effective_from)
        if key in target_keys:
            already_closed = party.effective_to is not None and party.effective_to <= utcnow()
            if already_closed:
                party.merged_into_customer_id = target_id
            else:
                _close_vehicle_party(
                    db,
                    party=party,
                    actor_id=actor_id,
                    group_id=group_id,
                    effective_to=party.effective_from,
                    merged_into_customer_id=target_id,
                )
            kept_closed.append(party.id)
        else:
            party.customer_id = target_id
            target_keys.add(key)
            repointed += 1
    return repointed, kept_closed


def _repoint_earlier_merges(
    db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID, actor_id: uuid.UUID, group_id: uuid.UUID
) -> list[uuid.UUID]:
    """KAN-383 (Anto, 2026-10-18, option A): a customer X merged into this
    duplicate D earlier names D in `duplicate_of_customer_id`; once D is
    merged into T, X would name a merged customer. Every such X is re-pointed
    to T in the merge's own transaction, its version bumped and the change
    audited on X (`merge_survivor_repointed`), so "merged into" always names a
    live customer. No event is published: X's rows already moved to D with its
    own merge and move on to T with this one (the `customer.merged` event for
    D carries them).

    The vehicle-party copies an earlier merge kept on X (KAN-177) name D in
    `merged_into_customer_id`; they are re-pointed to T the same way, so a
    `merged_party_row` refusal never names a merged customer.

    Read after the merge holds D's row FOR NO KEY UPDATE (KAN-372): a merge
    X→D still in flight holds D FOR SHARE, so this merge waits for it to
    commit and then finds X here — the race order KAN-372 could not refuse
    ends with X naming T as well. The X rows are locked in id order, after
    the merge's own party rows: the one place a merge locks a customer row
    outside "advisory → customer rows → party rows". No writer holding an X
    row waits on this merge: allocation, a reopen and a merge into or of X
    refuse a merged customer (a reopen of a kept copy locks that copy first,
    but after its advisory lock, and waits for nothing after it); a
    credit-block change on X takes only X's row; the nightly advisor-label
    refresh skips a customer it cannot lock and waits for nothing (KAN-403).
    The copies are only tried, never waited for: the nightly vehicle-party
    label refresh locks party rows in id order
    and could hold a copy while it waits for one of D's rows, so a copy held
    by another writer refuses the merge (409 `merge_vehicle_party_busy`,
    nothing written), which can simply be sent again."""

    earlier = list(
        db.scalars(
            select(Customer)
            .where(Customer.duplicate_of_customer_id == duplicate_id, Customer.group_id == group_id)
            .order_by(Customer.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    copies_naming_d = (
        select(VehicleParty)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(VehicleParty.merged_into_customer_id == duplicate_id, Customer.group_id == group_id)
        .order_by(VehicleParty.id)
    )
    # A merge into D is what writes such a copy, and it needs D's row, which
    # this merge holds: the set cannot grow between these two reads.
    copy_count = len(db.scalars(copies_naming_d.with_only_columns(VehicleParty.id)).all())
    kept_copies = list(db.scalars(copies_naming_d.with_for_update(of=VehicleParty, skip_locked=True)).all())
    if len(kept_copies) < copy_count:
        raise ConflictError(
            "A vehicle of this customer is being changed right now; send the merge again.",
            details={"reason": "merge_vehicle_party_busy"},
        )
    for copy in kept_copies:
        copy.merged_into_customer_id = target_id
    for merged in earlier:
        merged.duplicate_of_customer_id = target_id
        merged.updated_by = actor_id
        merged.version += 1
        record_audit_event(
            db,
            entity_type="customer",
            entity_id=merged.id,
            tenant_id=group_id,
            action="merge_survivor_repointed",
            actor_id=actor_id,
            before={"duplicateOfCustomerId": str(duplicate_id)},
            after={"duplicateOfCustomerId": str(target_id)},
        )
    return [merged.id for merged in earlier]


def _repoint_transactions(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> int:
    # Sales owns Transaction (one writer per fact) — customer only knows the
    # duplicate/target IDs from its own merge flow, so the actual row
    # mutation happens inside app.sales, reached through its public surface.
    # This is still a direct in-transaction cross-context write, same as
    # before the restructure; it's tracked residual coupling PR-4/PR-5 must
    # replace with a `customer.merged` event that sales consumes idempotently.
    #
    # Import is deferred to call time (not module top-level): sales's own
    # transaction service imports app.customer.public at top level to
    # validate customer_id on transaction create, so a top-level import here
    # would be a circular import. Both edges are still visible to
    # import-linter either way — it walks the whole AST, not just the
    # top-level imports.
    from app.sales.public import repoint_customer_transactions

    return repoint_customer_transactions(db, duplicate_id=duplicate_id, target_id=target_id)


def _repoint_contacts(
    db: Session, model: type, unique_fields: tuple[str, ...], *, duplicate_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[int, int]:
    """Shared logic for CustomerPhone/CustomerEmail/CustomerAddress:
    re-point onto the survivor unless it already has a row with the same
    identifying fields, in which case the duplicate's row is dropped as an
    exact duplicate. unique_fields is a tuple (not always length 1) because
    CustomerAddress has no single natural key the way phone_e164/
    email_address are — its dedup key is composite.

    Afterwards, is_primary is re-collapsed per (customer, type) — not once
    per customer — since a merge can leave more than one primary within a
    type-group when both sides already had one of that type (ADR-067).
    """

    def _key(row: Any) -> tuple:
        return tuple(getattr(row, field) for field in unique_fields)

    target_rows: list[Any] = list(
        db.scalars(select(model).where(model.customer_id == target_id)).all()  # type: ignore[attr-defined]
    )
    target_keys = {_key(row) for row in target_rows}
    repointed = dropped = 0
    duplicate_rows: list[Any] = list(
        db.scalars(select(model).where(model.customer_id == duplicate_id)).all()  # type: ignore[attr-defined]
    )
    for row in duplicate_rows:
        key = _key(row)
        if key in target_keys:
            db.delete(row)
            dropped += 1
        else:
            row.customer_id = target_id
            target_keys.add(key)
            repointed += 1
    if repointed:
        db.flush()
        type_column = _CONTACT_TYPE_COLUMN[model]
        target_rows_after: list[Any] = list(
            db.scalars(select(model).where(model.customer_id == target_id)).all()  # type: ignore[attr-defined]
        )
        types_present = {getattr(row, type_column) for row in target_rows_after}
        for type_value in types_present:
            _fixup_single_primary(db, model, customer_id=target_id, type_value=type_value)
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


def _repoint_customer_tags(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    """A tag already on the survivor is dropped from the duplicate rather
    than colliding on uq_customer_tag_customer_id_tag; the rest move over.
    """

    target_tags = {
        row.tag for row in db.scalars(select(CustomerTag).where(CustomerTag.customer_id == target_id)).all()
    }
    repointed = dropped = 0
    for row in db.scalars(select(CustomerTag).where(CustomerTag.customer_id == duplicate_id)).all():
        if row.tag in target_tags:
            db.delete(row)
            dropped += 1
        else:
            row.customer_id = target_id
            target_tags.add(row.tag)
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
    enhancement." Conflicting child rows (the same phone, email or
    external-id system already present on both) are dropped from the
    duplicate rather than erroring — the survivor's copy wins. A vehicle
    role+period present on both is the exception (KAN-177, ADR-064): the
    duplicate's copy is kept, closed and marked merged, never deleted — see
    _repoint_vehicle_parties.

    KAN-366: every refusal names a `details.reason`, so the merge dialog
    says it in the user's language instead of showing this English message.
    """

    if duplicate_of_customer_id == customer.id:
        raise BadRequestError("A customer cannot be merged into itself.", details={"reason": "merge_into_self"})

    target = _group_scoped_or_404(db, Customer, duplicate_of_customer_id, customer.group_id)
    # Read without a lock here; both statuses are checked again under the
    # customer-row locks the merge takes (KAN-372, _lock_merge_customers).
    if target.lifecycle_status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Cannot merge into a customer that has itself been merged.",
            details={"reason": "merge_target_merged", "duplicateOfCustomerId": str(duplicate_of_customer_id)},
        )
    if customer.lifecycle_status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "Customer has already been merged.",
            details={"reason": "customer_already_merged", "currentLifecycleStatus": "merged"},
        )

    before = {"lifecycleStatus": customer.lifecycle_status.value, "duplicateOfCustomerId": None}

    parties_repointed, parties_kept_closed = _repoint_vehicle_parties(
        db, duplicate_id=customer.id, target_id=target.id, actor_id=actor_id, group_id=customer.group_id
    )
    transactions_repointed = _repoint_transactions(db, duplicate_id=customer.id, target_id=target.id)
    phones_repointed, phones_dropped = _repoint_contacts(
        db, CustomerPhone, ("phone_e164",), duplicate_id=customer.id, target_id=target.id
    )
    emails_repointed, emails_dropped = _repoint_contacts(
        db, CustomerEmail, ("email_address",), duplicate_id=customer.id, target_id=target.id
    )
    addresses_repointed, addresses_dropped = _repoint_contacts(
        db,
        CustomerAddress,
        ("address_type", "address_street", "address_house_number", "address_postal_code", "address_country"),
        duplicate_id=customer.id,
        target_id=target.id,
    )
    external_ids_repointed, external_ids_dropped = _repoint_external_ids(
        db, duplicate_id=customer.id, target_id=target.id
    )
    tags_repointed, tags_dropped = _repoint_customer_tags(db, duplicate_id=customer.id, target_id=target.id)
    earlier_merges_repointed = _repoint_earlier_merges(
        db, duplicate_id=customer.id, target_id=target.id, actor_id=actor_id, group_id=customer.group_id
    )

    customer.lifecycle_status = CustomerLifecycleStatus.MERGED
    customer.duplicate_of_customer_id = duplicate_of_customer_id
    customer.updated_by = actor_id
    customer.version += 1

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="merge",
        actor_id=actor_id,
        before=before,
        after={
            "lifecycleStatus": "merged",
            "duplicateOfCustomerId": str(duplicate_of_customer_id),
            "vehiclePartiesRepointed": parties_repointed,
            # KAN-177: the overlapping rows are kept, closed and marked, not dropped.
            "vehiclePartiesKeptClosed": len(parties_kept_closed),
            "vehiclePartiesKeptClosedIds": [str(party_id) for party_id in parties_kept_closed],
            "transactionsRepointed": transactions_repointed,
            "phonesRepointed": phones_repointed,
            "phonesDropped": phones_dropped,
            "emailsRepointed": emails_repointed,
            "emailsDropped": emails_dropped,
            "addressesRepointed": addresses_repointed,
            "addressesDropped": addresses_dropped,
            "externalIdsRepointed": external_ids_repointed,
            "externalIdsDropped": external_ids_dropped,
            "tagsRepointed": tags_repointed,
            "tagsDropped": tags_dropped,
            # KAN-383: customers merged into this duplicate earlier now name the survivor.
            "earlierMergesRepointedIds": [str(customer_id) for customer_id in earlier_merges_repointed],
        },
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.merged",
            tenant_id=customer.group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="customer",
            aggregate_id=customer.id,
            # Survivor's label fields, with customerId/duplicateOfCustomerId
            # overriding the payload helper's own customerId — a consumer
            # needs the duplicate's id (what it may still be pointing at)
            # and the survivor's id (what to repoint to) in the same event.
            payload={
                **_customer_label_payload(target),
                "customerId": str(customer.id),
                "duplicateOfCustomerId": str(duplicate_of_customer_id),
            },
        ),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Merge conflicts with an existing record on the survivor.",
            details={"reason": "merge_conflict_on_survivor"},
        ) from exc
    db.refresh(customer)

    # WP-8 PR-7 — Pattern B (ADR-047, own commit), called AFTER the merge
    # above has already committed, never joining that transaction (unlike
    # _repoint_transactions above, which predates this rule being written
    # down for Transaction — that one is left as-is since the table it
    # touches is itself retired, ADR-050). A failure here is repaired by
    # nightly reconciliation, not by rolling back the merge.
    from app.sales.public import repoint_customer_sales_records

    repoint_customer_sales_records(db, duplicate_id=customer.id, target_id=target.id)

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
    db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID, phone_id: uuid.UUID
) -> CustomerPhone:
    phone = _group_scoped_or_404(db, CustomerPhone, phone_id, group_id)
    if phone.customer_id != customer_id:
        raise NotFoundError(f"CustomerPhone {phone_id} was not found.")
    return phone


def _consent_create_kwargs(data: Any) -> dict[str, Any]:
    """The four consent columns for a freshly written contact-channel row
    (FR-23 §1). The scope-required-on-grant rule is enforced at the schema;
    the timestamp is stamped here — "when" is part of the record revDSG
    asks for."""

    granted = bool(getattr(data, "consent_granted", False))
    return {
        "consent_granted": granted,
        "consent_scope": data.consent_scope,
        "consent_source": data.consent_source,
        "consent_timestamp": utcnow() if granted else None,
    }


def channel_authorises_marketing(row: Any) -> bool:
    """FR-23 §1 — THE consumer rule. A contact-channel row authorises a
    marketing send only when consent is granted AND its scope is
    `marketing` (a NULL scope means `marketing` on rows that predate the
    field). An invoicing- or service-scoped grant never authorises a
    campaign.

    No caller exists yet — Marketing is not built. Every future marketing
    selection must read consent through this, not `consent_granted`
    directly, or the whole point of FR-23 §1 is lost.
    """

    if not row.consent_granted:
        return False
    return row.consent_scope in (None, ConsentScope.MARKETING)


def _consent_snapshot(row: Any) -> dict[str, Any]:
    """The consent record as it stands, for an audit before/after (FR-11).
    A consent change is exactly the kind of change the audit log exists for."""

    return {
        "consentGranted": row.consent_granted,
        "consentScope": _plain(row.consent_scope),
        "consentSource": _plain(row.consent_source),
        "consentTimestamp": _plain(row.consent_timestamp),
    }


def _stamp_consent_timestamp_on_grant(row: Any, changes: dict[str, Any], *, was_granted: bool) -> None:
    """A false -> true consent transition stamps `consent_timestamp`; a
    revoke leaves the last-grant time in place as evidence."""

    if changes.get("consent_granted") is True and not was_granted:
        row.consent_timestamp = utcnow()


def create_customer_phone(
    db: Session, *, customer: Customer, data: CustomerPhoneCreate, actor_id: uuid.UUID
) -> CustomerPhone:
    is_primary = data.is_primary or not _has_usable_primary(
        db, CustomerPhone, customer_id=customer.id, type_value=data.phone_type
    )

    if is_primary:
        _unset_other_primaries(db, CustomerPhone, customer_id=customer.id, type_value=data.phone_type)

    phone = CustomerPhone(
        group_id=customer.group_id,
        customer_id=customer.id,
        phone_type=data.phone_type,
        label=data.label,
        phone_e164=data.phone_e164,
        phone_normalised=normalise_phone(data.phone_e164),
        is_primary=is_primary,
        created_by=actor_id,
        updated_by=actor_id,
        **_consent_create_kwargs(data),
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
        tenant_id=customer.group_id,
        action="phone_add",
        actor_id=actor_id,
        after={
            "phoneType": phone.phone_type.value,
            "label": phone.label,
            "phoneE164": phone.phone_e164,
            "isPrimary": phone.is_primary,
            **_consent_snapshot(phone),
        },
    )
    db.commit()
    db.refresh(phone)
    return phone


def update_customer_phone(
    db: Session, *, phone: CustomerPhone, data: CustomerPhoneUpdate, actor_id: uuid.UUID
) -> CustomerPhone:
    changes = data.model_dump(exclude_unset=True)
    was_granted = phone.consent_granted
    before = {
        "phoneType": phone.phone_type.value,
        "label": phone.label,
        "phoneE164": phone.phone_e164,
        "isPrimary": phone.is_primary,
        **_consent_snapshot(phone),
    }

    becomes_unusable = (changes.get("valid_to") is not None and phone.valid_to is None) or (
        changes.get("do_not_use") is True and not phone.do_not_use
    )
    if becomes_unusable:
        _assert_not_last_contact_point(db, phone, removing="phone number")
    groups_to_settle = _prepare_primary_change(
        db, CustomerPhone, row=phone, changes=changes, noun="phone"
    )

    for field, value in changes.items():
        setattr(phone, field, value)
    if "phone_e164" in changes:
        phone.phone_normalised = normalise_phone(phone.phone_e164)
    _stamp_consent_timestamp_on_grant(phone, changes, was_granted=was_granted)
    phone.updated_by = actor_id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This phone number is already on this customer.", details={"phoneE164": changes.get("phone_e164")}
        ) from exc

    for type_value in groups_to_settle:
        _fixup_single_primary(db, CustomerPhone, customer_id=phone.customer_id, type_value=type_value)
    db.flush()

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=phone.customer_id,
        tenant_id=phone.group_id,
        action="phone_update",
        actor_id=actor_id,
        before=before,
        after={
            "phoneType": phone.phone_type.value,
            "label": phone.label,
            "phoneE164": phone.phone_e164,
            "isPrimary": phone.is_primary,
            **_consent_snapshot(phone),
        },
    )
    db.commit()
    db.refresh(phone)
    return phone


def _assert_not_last_contact_point(db: Session, row: CustomerPhone | CustomerEmail, *, removing: str) -> None:
    """FR-03's "at least one contact point" is an invariant of the customer,
    not just of the create request — so deleting (or closing / marking
    do-not-use) the final one is rejected. Without this the rule would hold
    at creation and then quietly decay, which is how you end up with
    customers nobody can reach.

    Amended in WP-3 PR-5 (ADR-067): only a USABLE row counts — a closed row
    (valid_to set) or a do-not-use row no longer satisfies FR-03 even though
    it still exists, because staff cannot actually reach the customer
    through it.

    KAN-330: the guard is about the row being removed. A row that is already
    closed or do-not-use changes no usable count, so tidying it (closing,
    flagging or deleting it) is never refused; a usable row is refused only
    when no OTHER usable phone or email would remain.
    """

    if row.valid_to is not None or row.do_not_use:
        return

    def _usable_count(model: type) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(
                    model.customer_id == row.customer_id,  # type: ignore[attr-defined]
                    model.id != row.id,  # type: ignore[attr-defined]
                    model.valid_to.is_(None),  # type: ignore[attr-defined]
                    model.do_not_use.is_(False),  # type: ignore[attr-defined]
                )
            )
            or 0
        )

    if _usable_count(CustomerPhone) + _usable_count(CustomerEmail) == 0:
        raise BadRequestError(
            f"Cannot remove the last usable {removing} — a customer must keep at least one usable phone number or"
            " email address."
        )


def delete_customer_phone(db: Session, *, phone: CustomerPhone, actor_id: uuid.UUID) -> None:
    _assert_not_last_contact_point(db, phone, removing="phone number")
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=phone.customer_id,
        tenant_id=phone.group_id,
        action="phone_remove",
        actor_id=actor_id,
        before={
            "phoneType": phone.phone_type.value,
            "label": phone.label,
            "phoneE164": phone.phone_e164,
            "isPrimary": phone.is_primary,
        },
    )
    customer_id, phone_type = phone.customer_id, phone.phone_type
    db.delete(phone)
    db.flush()
    # KAN-46: deleting the primary would otherwise leave the type-group with
    # no primary and the projection null. Re-elect the oldest usable
    # survivor of that type in the same transaction.
    _fixup_single_primary(db, CustomerPhone, customer_id=customer_id, type_value=phone_type)
    db.commit()


def list_customer_emails(db: Session, *, customer_id: uuid.UUID) -> list[CustomerEmail]:
    stmt = select(CustomerEmail).where(CustomerEmail.customer_id == customer_id).order_by(CustomerEmail.created_at)
    return list(db.scalars(stmt).all())


def get_customer_email_or_404(
    db: Session, *, group_id: uuid.UUID, customer_id: uuid.UUID, email_id: uuid.UUID
) -> CustomerEmail:
    email = _group_scoped_or_404(db, CustomerEmail, email_id, group_id)
    if email.customer_id != customer_id:
        raise NotFoundError(f"CustomerEmail {email_id} was not found.")
    return email


def create_customer_email(
    db: Session, *, customer: Customer, data: CustomerEmailCreate, actor_id: uuid.UUID
) -> CustomerEmail:
    is_primary = data.is_primary or not _has_usable_primary(
        db, CustomerEmail, customer_id=customer.id, type_value=data.email_type
    )

    if is_primary:
        _unset_other_primaries(db, CustomerEmail, customer_id=customer.id, type_value=data.email_type)

    email = CustomerEmail(
        group_id=customer.group_id,
        customer_id=customer.id,
        email_type=data.email_type,
        label=data.label,
        email_address=data.email_address,
        is_primary=is_primary,
        created_by=actor_id,
        updated_by=actor_id,
        **_consent_create_kwargs(data),
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
        tenant_id=customer.group_id,
        action="email_add",
        actor_id=actor_id,
        after={
            "emailType": email.email_type.value,
            "label": email.label,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
            **_consent_snapshot(email),
        },
    )
    db.commit()
    db.refresh(email)
    return email


def update_customer_email(
    db: Session, *, email: CustomerEmail, data: CustomerEmailUpdate, actor_id: uuid.UUID
) -> CustomerEmail:
    changes = data.model_dump(exclude_unset=True)
    was_granted = email.consent_granted
    before = {
        "emailType": email.email_type.value,
        "label": email.label,
        "emailAddress": email.email_address,
        "isPrimary": email.is_primary,
        **_consent_snapshot(email),
    }

    becomes_unusable = (changes.get("valid_to") is not None and email.valid_to is None) or (
        changes.get("do_not_use") is True and not email.do_not_use
    )
    if becomes_unusable:
        _assert_not_last_contact_point(db, email, removing="email address")
    groups_to_settle = _prepare_primary_change(
        db, CustomerEmail, row=email, changes=changes, noun="email"
    )

    for field, value in changes.items():
        setattr(email, field, value)
    _stamp_consent_timestamp_on_grant(email, changes, was_granted=was_granted)
    email.updated_by = actor_id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This email address is already on this customer.",
            details={"emailAddress": changes.get("email_address")},
        ) from exc

    for type_value in groups_to_settle:
        _fixup_single_primary(db, CustomerEmail, customer_id=email.customer_id, type_value=type_value)
    db.flush()

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=email.customer_id,
        tenant_id=email.group_id,
        action="email_update",
        actor_id=actor_id,
        before=before,
        after={
            "emailType": email.email_type.value,
            "label": email.label,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
            **_consent_snapshot(email),
        },
    )
    db.commit()
    db.refresh(email)
    return email


def delete_customer_email(db: Session, *, email: CustomerEmail, actor_id: uuid.UUID) -> None:
    _assert_not_last_contact_point(db, email, removing="email address")
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=email.customer_id,
        tenant_id=email.group_id,
        action="email_remove",
        actor_id=actor_id,
        before={
            "emailType": email.email_type.value,
            "label": email.label,
            "emailAddress": email.email_address,
            "isPrimary": email.is_primary,
        },
    )
    customer_id, email_type = email.customer_id, email.email_type
    db.delete(email)
    db.flush()
    # KAN-46 — see delete_customer_phone.
    _fixup_single_primary(db, CustomerEmail, customer_id=customer_id, type_value=email_type)
    db.commit()


def _has_usable_primary(db: Session, model: type, *, customer_id: uuid.UUID, type_value: Any) -> bool:
    """Whether the (customer, type) group already has a usable primary. A
    create elects its new row (always usable) unless one does (KAN-151): a
    group whose rows are all closed or do_not_use has no primary, since
    `_fixup_single_primary` demoted them, so "no row of this type at all"
    would leave the new row unflagged. An existing usable primary is never
    demoted by a create that does not say `isPrimary: true`."""

    type_column = _CONTACT_TYPE_COLUMN[model]
    primaries: list[Any] = list(
        db.scalars(
            select(model).where(
                model.customer_id == customer_id,  # type: ignore[attr-defined]
                getattr(model, type_column) == type_value,
                model.is_primary.is_(True),  # type: ignore[attr-defined]
            )
        ).all()
    )
    return any(_is_usable_row(row) for row in primaries)


def _unset_other_primaries(db: Session, model: type, *, customer_id: uuid.UUID, type_value: Any) -> None:
    type_column = _CONTACT_TYPE_COLUMN[model]
    rows: list[Any] = list(
        db.scalars(
            select(model).where(
                model.customer_id == customer_id,  # type: ignore[attr-defined]
                getattr(model, type_column) == type_value,
                model.is_primary.is_(True),  # type: ignore[attr-defined]
            )
        ).all()
    )
    for row in rows:
        row.is_primary = False


def _prepare_primary_change(
    db: Session, model: type, *, row: Any, changes: dict[str, Any], noun: str
) -> list[Any]:
    """The primary handling shared by the phone / email / address update
    paths (ADR-067: exactly one primary per type-group, on every update).

    Runs before `changes` are applied to `row`. Returns the type-groups the
    caller must settle with `_fixup_single_primary` once the row is flushed.

    A PATCH that changes the type touches two groups (KAN-102). The group
    the row leaves re-elects if its primary left. In the group it joins,
    `isPrimary: true` in the same PATCH makes the moved row the primary;
    otherwise that group's existing usable primary stays and the moved row
    is demoted — a type change never silently demotes another row (Anto,
    KAN-102). A moved row joining a group with no primary is elected by the
    fixup, whether or not it was primary before.

    Usability is judged AFTER the PATCH, not by what the PATCH changes
    (KAN-113): a row that is closed or do_not_use once the changes apply —
    whether this PATCH makes it so or an earlier one did — cannot take the
    flag, so `isPrimary: true` on it leaves the group's existing usable
    primary alone and the fixup demotes it again.

    Reopening a row (`validTo: null`, `doNotUse: false`) settles its group
    too (KAN-113), the mirror of the KAN-46 re-election: a group that had
    no usable primary elects the reopened row; one that has a usable
    primary keeps it — a reopened row never takes the flag unless the same
    PATCH says `isPrimary: true`.
    """

    type_column = _CONTACT_TYPE_COLUMN[model]
    old_type = getattr(row, type_column)
    new_type = changes.get(type_column, old_type)
    moves = new_type != old_type

    usable_before = _is_usable_row(row)
    usable_after = changes.get("valid_to", row.valid_to) is None and not changes.get("do_not_use", row.do_not_use)

    if changes.get("is_primary") is True:
        if (not row.is_primary or moves) and usable_after:
            _unset_other_primaries(db, model, customer_id=row.customer_id, type_value=new_type)
    elif changes.get("is_primary") is False and row.is_primary:
        raise BadRequestError(
            f"Cannot unset the primary {noun} directly — mark a different {noun} as primary instead."
        )
    elif moves and row.is_primary:
        target_primaries: list[Any] = list(
            db.scalars(
                select(model).where(
                    model.customer_id == row.customer_id,  # type: ignore[attr-defined]
                    getattr(model, type_column) == new_type,
                    model.is_primary.is_(True),  # type: ignore[attr-defined]
                )
            ).all()
        )
        if any(_is_usable_row(r) for r in target_primaries):
            row.is_primary = False

    if moves:
        return [old_type, new_type]
    if usable_before != usable_after:
        # KAN-46: a closed / do_not_use row is never primary. If it was (or
        # this PATCH flags it), re-elect a usable survivor of the same type
        # so the Mobile/Landline/Work projection follows the working number
        # instead of going null. KAN-113: the reverse — a reopened row in a
        # group with no usable primary is elected by the same fixup. A no-op
        # when the group already has its one usable primary.
        return [new_type]
    if "is_primary" in changes:
        # KAN-113: `isPrimary: true` on a row that is not usable after the
        # PATCH is applied to the row like any other field, so the fixup
        # takes the flag back off it; the group's usable primary was never
        # unset above. A no-op for a usable row, which took the flag.
        return [new_type]
    return []


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
    groups_to_settle = _prepare_primary_change(
        db, CustomerAddress, row=address, changes=changes, noun="address"
    )

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


_EMPTY_PROJECTIONS: dict[str, Any] = {
    "phone_mobile": None,
    "phone_landline": None,
    "phone_work": None,
    "email": None,
    "email_secondary": None,
    "address": None,
    "tags": [],
}


def compute_customer_projections(db: Session, customer_id: uuid.UUID) -> dict[str, Any]:
    """Single-customer form of compute_customer_projections_batch — for the
    get/create/update/merge endpoints, which only ever need one customer's
    projections at a time.
    """

    return compute_customer_projections_batch(db, [customer_id]).get(customer_id, dict(_EMPTY_PROJECTIONS))


def compute_customer_projections_batch(
    db: Session, customer_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """The six read-model projections (ADR-067), computed here and never
    stored: phoneMobile/phoneLandline/phoneWork, email, emailSecondary,
    address (primary domicile). Only a USABLE row (not closed, not
    do-not-use) is eligible — same definition FR-03 uses.

    Per-type resolution (PRD-Customers FR-23 §2, the prototype's
    `NX.chPrimary`): among the usable rows of a type, the one flagged
    primary; failing that, the OLDEST one by created_at; failing that,
    null. The write path re-elects a primary when one is closed / flagged
    / deleted (KAN-46), so the oldest-row fallback only carries a
    type-group whose rows history left unflagged — but it must, because a
    working number must never render as an empty column.

    `email` / `emailSecondary` (FR-23 §3): `email` is the resolved
    `personal` row, or the resolved `work` row when there is no usable
    personal one — a business customer typically has no personal address,
    and Finance needs the work one. `emailSecondary` is the resolved
    `work` row, suppressed when it is the same row `email` resolved to.

    Batched — one query per table for the whole set of customer_ids, not one
    per customer — same N+1-avoidance reasoning as _primary_contact_maps,
    since the grid renders these as flat columns for a whole page of rows.

    The billing-with-fallback-to-domicile variant the brief describes for
    document rendering is a document-rendering concern (WP-6b/WP-6c), not a
    customer-record concern — this returns the plain domicile projection
    only.
    """

    if not customer_ids:
        return {}

    def _usable(rows: list[Any]) -> list[Any]:
        return [r for r in rows if _is_usable_row(r)]

    def _group_by_customer(rows: list[Any]) -> dict[uuid.UUID, list[Any]]:
        grouped: dict[uuid.UUID, list[Any]] = {}
        for row in rows:
            grouped.setdefault(row.customer_id, []).append(row)
        return grouped

    # Ordered by created_at so the "oldest usable row" fallback below is
    # deterministic and matches _fixup_single_primary's own tie-break.
    phones_by_customer = _group_by_customer(
        _usable(
            list(
                db.scalars(
                    select(CustomerPhone)
                    .where(CustomerPhone.customer_id.in_(customer_ids))
                    .order_by(CustomerPhone.created_at)
                ).all()
            )
        )
    )
    emails_by_customer = _group_by_customer(
        _usable(
            list(
                db.scalars(
                    select(CustomerEmail)
                    .where(CustomerEmail.customer_id.in_(customer_ids))
                    .order_by(CustomerEmail.created_at)
                ).all()
            )
        )
    )
    addresses_by_customer = _group_by_customer(
        _usable(
            list(
                db.scalars(
                    select(CustomerAddress)
                    .where(CustomerAddress.customer_id.in_(customer_ids))
                    .order_by(CustomerAddress.created_at)
                ).all()
            )
        )
    )
    tags_by_customer: dict[uuid.UUID, list[str]] = {}
    for row in db.scalars(select(CustomerTag).where(CustomerTag.customer_id.in_(customer_ids))).all():
        tags_by_customer.setdefault(row.customer_id, []).append(row.tag)

    def _primary_of_type(rows: list[Any], type_column: str, type_value: Any) -> Any | None:
        of_type = [r for r in rows if getattr(r, type_column) == type_value]
        return next((r for r in of_type if r.is_primary), of_type[0] if of_type else None)

    result: dict[uuid.UUID, dict[str, Any]] = {}
    for customer_id in customer_ids:
        phones = phones_by_customer.get(customer_id, [])
        emails = emails_by_customer.get(customer_id, [])
        addresses = addresses_by_customer.get(customer_id, [])
        phone_mobile = _primary_of_type(phones, "phone_type", PhoneType.MOBILE)
        phone_landline = _primary_of_type(phones, "phone_type", PhoneType.LANDLINE)
        phone_work = _primary_of_type(phones, "phone_type", PhoneType.WORK)
        email_personal = _primary_of_type(emails, "email_type", EmailType.PERSONAL)
        email_work = _primary_of_type(emails, "email_type", EmailType.WORK)
        email_row = email_personal or email_work
        email_secondary_row = email_work if email_work is not email_row else None
        address_domicile = _primary_of_type(addresses, "address_type", AddressType.DOMICILE)
        result[customer_id] = {
            "phone_mobile": phone_mobile.phone_e164 if phone_mobile else None,
            "phone_landline": phone_landline.phone_e164 if phone_landline else None,
            "phone_work": phone_work.phone_e164 if phone_work else None,
            "email": email_row.email_address if email_row else None,
            "email_secondary": email_secondary_row.email_address if email_secondary_row else None,
            "address": CustomerAddressRead.model_validate(address_domicile) if address_domicile else None,
            "tags": sorted(tags_by_customer.get(customer_id, [])),
        }
    return result


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
            details={"systemName": data.system_name, "externalId": data.external_id},
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


# --- VehicleParty: customer-to-vehicle relationships (Customer PRD D-12,
# FR-10). Vehicle is tenant-agnostic (no tenant_id — a VIN is global, see
# app/models/vehicle.py), so the tenant boundary here is enforced purely by
# resolving `customer` through get_customer_or_404 before any of these run;
# the vehicle_id itself is looked up with no tenant filter, same as every
# other vehicle lookup in the codebase.


def _validate_effective_range(effective_from, effective_to) -> None:
    # A client never writes a zero-length period. The one such row is the
    # copy a customer merge keeps (KAN-177: effective_to = effective_from),
    # written by the merge itself, so a PATCH of that row's dates is refused
    # here like any other empty period.
    if effective_to is not None and effective_to <= effective_from:
        raise BadRequestError(
            "effective_to must be after effective_from (a zero-length period is only ever the copy a "
            "customer merge keeps, written by the merge itself)."
        )


def list_customer_vehicles(
    db: Session, *, customer_id: uuid.UUID, include_closed: bool = False
) -> list[VehicleParty]:
    """Default is CURRENT allocations only (effective_to is null or still
    in the future) — WP-5 PR-9, ADR-064. include_closed=True is FR-V-16's
    "former allocations" view on Vehicle 360's Identity tab; the row
    itself is never deleted (see delete_customer_vehicle below), so
    history is always available on request, just not mixed into the
    default view by default.
    """

    stmt = (
        select(VehicleParty)
        .where(VehicleParty.customer_id == customer_id)
        .order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > utcnow()))
    parties = list(db.scalars(stmt).all())
    _fill_unlabelled_vehicle_parties_for_read(db, parties)
    return parties


# --- KAN-84: the vehicle label on VehicleParty (CLAUDE.md rule 2). The
# vehicle's VIN/number/make/model/year/trim are copied onto the party row
# from app.vehicle.public.get_vehicle_summaries — never joined at read time.

_VEHICLE_LABEL_FIELDS = {
    "vehicle_vin": "vin",
    "vehicle_number": "vehicle_number",
    "vehicle_make": "make",
    "vehicle_model": "model",
    "vehicle_model_year": "model_year",
    "vehicle_trim": "trim",
}


def _label_changes(party: VehicleParty, summary: VehicleSummary) -> dict[str, object]:
    return {
        column: getattr(summary, field)
        for column, field in _VEHICLE_LABEL_FIELDS.items()
        if getattr(party, column) != getattr(summary, field)
    }


def _write_vehicle_labels(db: Session, parties: list[VehicleParty]) -> None:
    """Writes the current label onto each party row (dirty — committed by the
    caller's own transaction). A party whose vehicle no longer exists keeps
    whatever label it had: the nightly reconciliation reports the dangling id."""

    summaries = get_vehicle_summaries(db, [p.vehicle_id for p in parties])
    now = utcnow()
    for party in parties:
        summary = summaries.get(party.vehicle_id)
        if summary is None:
            continue
        for column, value in _label_changes(party, summary).items():
            setattr(party, column, value)
        party.vehicle_label_refreshed_at = now
        party.__dict__.pop("_vehicle_label_read_fill", None)


def _fill_unlabelled_vehicle_parties_for_read(db: Session, parties: list[VehicleParty]) -> None:
    """A row written before KAN-84 has no label until the nightly refresh
    reaches it. A read fills it in memory only: the label is held in a plain
    instance attribute that VehicleParty.vehicle prefers, outside ORM state,
    so nothing is flushed (a GET never writes) and a later write to the same
    object in the same session still sees the stored columns as empty and
    writes them."""

    unlabelled = [p for p in parties if p.vehicle_label_refreshed_at is None]
    if not unlabelled:
        return
    summaries = get_vehicle_summaries(db, [p.vehicle_id for p in unlabelled])
    for party in unlabelled:
        summary = summaries.get(party.vehicle_id)
        if summary is None:
            continue
        party.__dict__["_vehicle_label_read_fill"] = VehiclePartyVehicleLabel(
            id=party.vehicle_id,
            vin=summary.vin,
            vehicle_number=summary.vehicle_number,
            make=summary.make,
            model=summary.model,
            model_year=summary.model_year,
            trim=summary.trim,
        )


def oldest_vehicle_party_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest stored vehicle label — what the worker heartbeat
    records as `dms.label.age_seconds{label="vehicle_party.vehicle"}` (CLAUDE.md
    rule 10). The nightly job restamps every row it can resolve, so a value
    well past a day means the job has stopped. None when no row has ever been
    labelled. Rows whose vehicle no longer exists are never restamped; they
    are the reconciliation's finding, not this alarm's, and are excluded."""

    oldest = db.scalar(
        select(func.min(VehicleParty.vehicle_label_refreshed_at)).where(
            VehicleParty.vehicle_label_refreshed_at.is_not(None)
        )
    )
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.UTC)
    return (utcnow() - oldest).total_seconds()


_LABEL_REFRESH_BATCH = 500


def refresh_vehicle_party_labels(db: Session) -> int:
    """KAN-84 — the nightly job (registered in app.worker) that keeps every
    VehicleParty's vehicle label in step with the vehicle context, so a
    catalogue match or VIN correction made after the link shows within a
    day. Also the backfill for rows written before KAN-84. Walks the table
    in id order, one commit per batch; returns how many rows' labels changed.

    A row whose label changed is updated through the ORM (updated_at moves:
    what the API returns for it changed). A row whose label is already
    current only gets vehicle_label_refreshed_at stamped, with updated_at
    deliberately held — nothing a reader sees changed.
    """

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = select(VehicleParty).order_by(VehicleParty.id).limit(_LABEL_REFRESH_BATCH)
        if last_id is not None:
            stmt = stmt.where(VehicleParty.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        summaries = get_vehicle_summaries(db, [p.vehicle_id for p in batch])
        now = utcnow()
        unchanged_ids: list[uuid.UUID] = []
        for party in batch:
            summary = summaries.get(party.vehicle_id)
            if summary is None:
                continue
            party.__dict__.pop("_vehicle_label_read_fill", None)
            changes = _label_changes(party, summary)
            if changes:
                for column, value in changes.items():
                    setattr(party, column, value)
                party.vehicle_label_refreshed_at = now
                changed += 1
            else:
                unchanged_ids.append(party.id)
        db.flush()
        if unchanged_ids:
            db.execute(
                update(VehicleParty)
                .where(VehicleParty.id.in_(unchanged_ids))
                .values(vehicle_label_refreshed_at=now, updated_at=VehicleParty.updated_at)
                .execution_options(synchronize_session=False)
            )
        db.commit()


def list_vehicle_parties(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[VehicleParty]:
    """The vehicle-side mirror of list_customer_vehicles — FR-V-16's
    Vehicle 360 Identity tab needs "who holds which role on THIS car",
    keyed by vehicle rather than by customer. Same default-open-only /
    include_closed=True shape.

    group_id is required, not optional, on purpose — same reasoning as
    list_other_vehicle_parties_batch just below: vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no
    group_id column at all, so two entirely unrelated dealer groups can
    genuinely attach a party row to the SAME vehicle_id. Without this
    join, one group's advisor would see another group's customer's id
    (and, via the Identity tab's customer overlay, their name) as a party
    on a car neither dealership has any relationship over — the exact
    cross-tenant leak rule #7 and ADR-049 forbid. A row whose customer
    belongs to another group (or is dangling) is silently dropped, never
    a 403/404 on the whole vehicle: this is a read-model projection of a
    globally-keyed fact, not a request FOR that other group's customer
    record.
    """

    stmt = _vehicle_parties_in_group_stmt(
        VehicleParty, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
    )
    return list(db.scalars(stmt).all())


def list_vehicle_party_holders(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[tuple[VehicleParty, str]]:
    """KAN-140 — list_vehicle_parties plus each holder's display name, for
    the Identity tab (an internal id is never user-visible text). Built
    from the SAME group-scoped statement, so a name can only come from a
    customer the row filter already admits: another group's holder is
    dropped before its name is ever read.
    """

    stmt = _vehicle_parties_in_group_stmt(
        VehicleParty, Customer, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
    ).options(
        # Only what customer_display_name reads — never the whole row (which
        # would decrypt tax_id just to build a label).
        load_only(Customer.company_name, Customer.first_name, Customer.last_name, Customer.customer_number)
    )
    return [(party, customer_display_name(customer)) for party, customer in db.execute(stmt).all()]


def _vehicle_parties_in_group_stmt(
    *entities: type[VehicleParty] | type[Customer], vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool
) -> Select:
    """The one group-scoped read behind list_vehicle_parties and
    list_vehicle_party_holders — see list_vehicle_parties for why the
    Customer join is the group boundary."""

    stmt = (
        select(*entities)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(VehicleParty.vehicle_id == vehicle_id, Customer.group_id == group_id)
        .order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > utcnow()))
    return stmt


def customer_in_group(
    customer_id: ColumnElement[Any] | InstrumentedAttribute[Any], group_id: ColumnElement[Any]
) -> Exists:
    """KAN-259 — a SQL predicate, for another context's nightly
    reconciliation (Sales'): the customer `customer_id` names belongs to
    group `group_id`. Customers are group-scoped (ADR-014), so a deal's
    customer is right only in the group of the deal's dealership, which the
    caller passes as an expression. Builds an EXISTS; reads nothing itself."""

    return select(Customer.id).where(Customer.id == customer_id, Customer.group_id == group_id).exists()


def customer_display_name(customer: Customer) -> str:
    """Company name, else first+last, else customer number — the same
    precedence `resolve_customer_label` uses in `sales`, which keeps its own
    copy rather than importing this. Exported through customer.public for
    the vehicle context's party-roles and allocate responses (KAN-140)."""

    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def list_other_vehicle_parties_batch(
    db: Session, *, vehicle_ids: list[uuid.UUID], exclude_customer_id: uuid.UUID, group_id: uuid.UUID
) -> dict[uuid.UUID, list[OtherVehiclePartySummary]]:
    """KAN-49 / FR-19 — "who else is a party on the same car", for every
    vehicle on the Vehicles tab in ONE pass (2 queries total, never N+1
    regardless of how many vehicles the customer has). customer_id on
    VehicleParty is intra-context (customer -> customer), so this is a
    plain batched read, not the three-column denormalization pattern that
    rule #2/#3 reserve for cross-context references.

    Open parties only — exit criterion 1: a closed row is excluded from
    "others", it is not a party now (this customer's own closed rows are a
    separate, deliberately-included concern — see the /vehicles route).

    group_id is required, not optional, on purpose. vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no group_id
    column at all, so two entirely unrelated dealer groups can genuinely
    attach a party row to the SAME vehicle_id — without this filter, one
    group's advisor would see another group's customer's name and id as
    an "other party" on a car neither dealership actually shares a
    relationship over. Customers ARE group-scoped (ADR-014), so the
    Customer half of this join is where the boundary has to be drawn.
    A customer from a different group is silently skipped, the same as
    the existing dangling-customer-id case below — never a 404 or an
    error, since this is a read-model nicety on someone else's own tab,
    not a request FOR that other customer's record.
    """

    if not vehicle_ids:
        return {}

    party_rows = list(
        db.scalars(
            select(VehicleParty)
            .where(
                VehicleParty.vehicle_id.in_(vehicle_ids),
                VehicleParty.customer_id != exclude_customer_id,
                or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > utcnow()),
            )
            .order_by(VehicleParty.role)
        ).all()
    )
    if not party_rows:
        return {}

    other_customer_ids = {p.customer_id for p in party_rows}
    customers_by_id = {
        c.id: c
        for c in db.scalars(
            select(Customer).where(Customer.id.in_(other_customer_ids), Customer.group_id == group_id)
        ).all()
    }

    by_vehicle: dict[uuid.UUID, list[OtherVehiclePartySummary]] = {}
    for party in party_rows:
        other_customer = customers_by_id.get(party.customer_id)
        # A dangling customer_id (deleted/merged elsewhere) is silently
        # skipped rather than rendering a broken party row — the write
        # path never leaves this dangling in practice, but a read-model
        # projection should never 500 on stale data.
        if other_customer is None:
            continue
        by_vehicle.setdefault(party.vehicle_id, []).append(
            OtherVehiclePartySummary(customer_id=party.customer_id, role=party.role, display_name=customer_display_name(other_customer))
        )
    return by_vehicle


def get_customer_vehicle_or_404(db: Session, *, customer_id: uuid.UUID, party_id: uuid.UUID) -> VehicleParty:
    """Only the write routes (PATCH, DELETE) load a single party, so a row
    written before KAN-84 gets its vehicle label written here, committed by
    that write — the response is built after the caller's commit/refresh.
    The row is read unlocked; each write then locks and re-reads it
    (`_lock_party`, KAN-148), which labels it again after the re-read."""

    party = db.scalar(
        select(VehicleParty).where(VehicleParty.id == party_id, VehicleParty.customer_id == customer_id)
    )
    if party is None:
        raise NotFoundError(f"VehicleParty {party_id} was not found.")
    if party.vehicle_label_refreshed_at is None:
        _write_vehicle_labels(db, [party])
    return party


def _lock_party(db: Session, party: VehicleParty) -> VehicleParty:
    """KAN-148: the party row, locked FOR UPDATE and re-read, so a write
    decides on the committed row and not on what this session read before a
    concurrent Disconnect or allocation committed. The re-read discards any
    unflushed change on the row, so a row written before KAN-84 is labelled
    again here (see get_customer_vehicle_or_404)."""

    locked = db.scalar(
        select(VehicleParty)
        .where(VehicleParty.id == party.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:  # pragma: no cover - party rows are never deleted (ADR-064)
        raise NotFoundError(f"VehicleParty {party.id} was not found.")
    if locked.vehicle_label_refreshed_at is None:
        _write_vehicle_labels(db, [locked])
    return locked


def _is_open(effective_to: dt.datetime | None) -> bool:
    """A row holds its role now when it has no end, or an end still to come
    — the same test every read and the incumbent lookup apply."""

    return effective_to is None or effective_to > utcnow()


def create_customer_vehicle(
    db: Session, *, customer: Customer, data: CustomerVehicleCreate, actor_id: uuid.UUID
) -> VehicleParty:
    """KAN-31: resolves against vehicle_mdm (WP-5's three-layer model),
    never the legacy `vehicle` table — that table's writes are frozen
    (ADR-021) and `LinkVehicleModal` has only ever POSTed a vehicle_mdm.id,
    so resolving against the legacy table 404'd on every real attempt.
    """

    vehicle = get_vehicle_mdm_or_404(db, data.vehicle_id)
    effective_from = data.effective_from or utcnow()
    _validate_effective_range(effective_from, data.effective_to)

    if _is_open(data.effective_to):
        # The ordinary path — every real caller (LinkVehicleModal never
        # sends effectiveTo on create). Delegates to the SAME function the
        # vehicle-side POST /vehicle-mdm/{id}/allocate calls, so both
        # directions share one ADR-064 implementation: a new holder CLOSES
        # whichever other open holder of this (vehicle, role) exists,
        # never a second silent open row. Before this fix, this endpoint
        # inserted a raw VehicleParty and never closed the incumbent —
        # exactly what ADR-064 exists to prevent.
        # KAN-148 (Anto, option A): an end date still to come is a holder
        # NOW, so it takes the same path — locked, closing the others —
        # and keeps its end date.
        return allocate_vehicle_party(
            db,
            vehicle_id=vehicle.id,
            customer_id=customer.id,
            role=data.role,
            group_id=customer.group_id,
            actor_id=actor_id,
            effective_from=effective_from,
            effective_to=data.effective_to,
        )

    # An effectiveTo already past on CREATE is a backdated, already-closed
    # row — history, not a holder: closing some OTHER open holder to make
    # room for a row that is itself already closed would misrepresent the
    # timeline. No real caller sends this today (kept for the documented API
    # contract this schema field is part of — see
    # test_create_rejects_effective_to_before_effective_from).
    # KAN-370: under the allocation's locks — the (vehicle, role, group)
    # advisory lock, then the customer row FOR SHARE — and refused for a
    # merged customer (409 `customer_merged`), so a history row never lands
    # on a merged record, and one written while a merge of this customer runs
    # either commits before the merge locks the customer (and is re-pointed
    # with the rest) or waits for it and is refused.
    _lock_vehicle_role_in_group(db, vehicle_id=vehicle.id, role=data.role, group_id=customer.group_id)
    _refuse_merged_customer(db, customer.id)
    party = VehicleParty(
        vehicle_id=vehicle.id,
        customer_id=customer.id,
        role=data.role,
        effective_from=effective_from,
        effective_to=data.effective_to,
    )
    _write_vehicle_labels(db, [party])
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={
                "reason": "vehicle_party_exists",
                "vehicleId": str(vehicle.id),
                "role": data.role.value,
                "effectiveFrom": effective_from.isoformat(),
            },
        ) from exc

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="vehicle_party_add",
        actor_id=actor_id,
        after={
            "vehicleId": str(vehicle.id),
            "role": party.role.value,
            "effectiveFrom": party.effective_from.isoformat(),
            "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        },
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.linked",
            tenant_id=customer.group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )
    db.commit()
    db.refresh(party)
    return party


def update_customer_vehicle(
    db: Session, *, party: VehicleParty, data: CustomerVehicleUpdate, actor_id: uuid.UUID, group_id: uuid.UUID
) -> VehicleParty:
    """KAN-148 (Anto, option A): a PATCH whose `effectiveTo` leaves the row
    holding its role — `null`, or an end still to come — may reopen a closed
    row (say, to undo a Disconnect clicked by mistake), and goes through the
    allocation's own close rule: under the same advisory lock on (vehicle,
    role, group), every OTHER open holder of the role in the group is closed
    in the same transaction, so the group never shows two current holders.
    A reopen publishes `customer.vehicle_party.linked`, as an allocation
    does. The advisory lock is taken before the row lock, in the order the
    allocation takes them, so the two never deadlock. The copy a customer
    merge kept (KAN-177, `merged_into_customer_id`) is never reopened: 409
    `merged_party_row`. KAN-352: a PATCH that ends a holding row (an
    `effectiveTo` now or past) publishes `customer.vehicle_party.unlinked`,
    as Disconnect does."""

    changes = data.model_dump(exclude_unset=True)
    holds_after = "effective_to" in changes and _is_open(changes["effective_to"])
    if holds_after:
        _lock_vehicle_role_in_group(db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id)
    party = _lock_party(db, party)
    held_before = _is_open(party.effective_to)
    if holds_after and party.merged_into_customer_id is not None:
        # KAN-177 keeps the duplicate's copy closed at its start so it never
        # reads as a current party again; reopening it would also close the
        # survivor's holder of the same role.
        raise ConflictError(
            "This row is the copy a customer merge kept; it cannot hold its role again.",
            details={
                "reason": "merged_party_row",
                "mergedIntoCustomerId": str(party.merged_into_customer_id),
            },
        )
    if holds_after:
        # KAN-365: checked after KAN-177's row-level refusal, so the copy a
        # merge kept still answers `merged_party_row`.
        _refuse_merged_customer(db, party.customer_id)
    before = {
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }

    new_effective_from = changes.get("effective_from", party.effective_from)
    new_effective_to = changes.get("effective_to", party.effective_to)
    _validate_effective_range(new_effective_from, new_effective_to)

    for field, value in changes.items():
        setattr(party, field, value)

    if holds_after:
        for other in _open_holders_in_group(
            db, vehicle_id=party.vehicle_id, role=party.role, group_id=group_id, excluding=party.id
        ):
            _close_vehicle_party(db, party=other, actor_id=actor_id, group_id=group_id)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={"reason": "vehicle_party_exists", "vehicleId": str(party.vehicle_id), "role": party.role.value},
        ) from exc

    # KAN-352: the holder changes in either direction, so the event does
    # too — a reopen is `linked`, an end now or in the past is `unlinked`, as
    # Disconnect publishes it. A PATCH that leaves the row holding (or not)
    # publishes nothing new.
    ends_now = held_before and "effective_to" in changes and not holds_after
    if (holds_after and not held_before) or ends_now:
        publish_event(
            db,
            OutboxEvent(
                event_type="customer.vehicle_party.linked" if holds_after else "customer.vehicle_party.unlinked",
                tenant_id=group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="vehicle_party",
                aggregate_id=party.id,
                payload=_vehicle_party_payload(party),
            ),
        )
    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
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


def delete_customer_vehicle(db: Session, *, party: VehicleParty, actor_id: uuid.UUID, group_id: uuid.UUID) -> None:
    """"Disconnect" (FR-V-05) — CLOSES the allocation by setting
    effective_to; the row is never deleted, so history survives (WP-5
    PR-9, ADR-064). This was a hard db.delete() before PR-9 — a real
    correctness gap against ADR-064's explicit "never overwritten, never
    deleted" rule, fixed here rather than left for whoever built PR-9's
    party-role work to rediscover. list_customer_vehicles' default
    (open-only) view already filters closed rows out, so callers see the
    same "it's gone" result as before; GET .../vehicles?includeClosed=true
    is the only way the row becomes visible again.

    Idempotent against a second call on an already-closed row — the
    closing timestamp isn't allowed to drift forward on a repeat request.
    KAN-148: the row is locked and re-read first, so a Disconnect racing
    another Disconnect, or an allocation closing this row, waits for it and
    then finds the row closed — one close, one `unlinked` event.
    """

    party = _lock_party(db, party)
    if not _is_open(party.effective_to):
        return

    _close_vehicle_party(db, party=party, actor_id=actor_id, group_id=group_id)
    db.commit()


def _close_vehicle_party(
    db: Session,
    *,
    party: VehicleParty,
    actor_id: uuid.UUID,
    group_id: uuid.UUID,
    effective_to: dt.datetime | None = None,
    merged_into_customer_id: uuid.UUID | None = None,
) -> None:
    """Sets effective_to and writes the audit row and the unlinked outbox
    event — WITHOUT committing, so allocate_vehicle_party can close the
    incumbent and insert the new holder in one transaction (KAN-99). A
    commit between the two would leave the vehicle with no holder at all
    if the insert then failed. `group_id` must be the group of the
    customer on `party`: it stamps the audit row and the event.

    `effective_to` defaults to now. A customer merge (KAN-177) passes the
    row's own start and the survivor it merged into, which the audit row
    and the event carry as `mergedIntoCustomerId`.
    """

    before = {
        "vehicleId": str(party.vehicle_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
    }
    party.effective_to = effective_to or utcnow()
    after: dict[str, Any] = {"effectiveTo": party.effective_to.isoformat()}
    if merged_into_customer_id is not None:
        party.merged_into_customer_id = merged_into_customer_id
        after["mergedIntoCustomerId"] = str(merged_into_customer_id)

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=party.customer_id,
        tenant_id=group_id,
        action="vehicle_party_remove",
        actor_id=actor_id,
        before=before,
        after=after,
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.unlinked",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )


def allocate_vehicle_party(
    db: Session, *, vehicle_id: uuid.UUID, customer_id: uuid.UUID, role: VehiclePartyRole,
    group_id: uuid.UUID, actor_id: uuid.UUID, effective_from: dt.datetime | None = None,
    effective_to: dt.datetime | None = None,
) -> VehicleParty:
    """The actual ADR-064 allocation: setting a new holder for a role
    CLOSES whichever OTHER open holder of that same (vehicle, role)
    currently exists — never an update of the existing row, never a
    silent overwrite. If the SAME customer already holds this role
    (re-confirming, not a handover), this is a no-op returning the
    existing open row rather than closing-then-reopening an identical
    allocation. One dialog, reachable from either the vehicle or the
    customer (FR-V-05) — this is the function both call.

    KAN-99 — holders are per dealer group (Anto's ruling, 2026-10-04;
    ADR-014): vehicle_mdm is a global fact (ADR-022) and VehicleParty has
    no group column, so two groups can each hold the same role on one
    VIN. The customer is resolved in the caller's group first (404, never
    a cross-group link — the trade-in path passes a request-body id), and
    the incumbents are looked up through Customer.group_id, the same
    intra-context join as list_vehicle_parties. So an allocation closes
    EVERY open holder of (vehicle, role) in the caller's group — a
    backdated create or a reopened row can leave more than one — and
    never touches another group's timeline. The close and the insert
    commit together: a failure leaves nothing committed, and the
    request's session rolls it back.

    Concurrency: a row lock cannot serialise this — a concurrent request
    never sees the row the other one is inserting, with or without an
    incumbent — so on Postgres a transaction-scoped advisory lock on
    (vehicle, role, group) is taken before the incumbent lookup. A second
    allocation waits for the first to commit, then sees its row and
    closes it. The incumbents are also locked FOR UPDATE, so an allocation
    never re-closes a row a concurrent Disconnect has just closed, and a
    Disconnect locks its row too (KAN-148), so it never re-closes one an
    allocation has just closed.

    KAN-148 (Anto, option A): `effective_to` is an end still to come (a
    create with a future end date) — the row holds the role now, so the
    incumbents are closed exactly as for an open-ended holder. The
    same-customer no-op applies only to an open-ended request: an end date
    the incumbent lacks is a new statement, so it is a new row.
    """

    get_customer_or_404(db, group_id, customer_id)
    _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    _refuse_merged_customer(db, customer_id)
    effective_from = effective_from or utcnow()
    incumbents = _open_holders_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
    if effective_to is None and len(incumbents) == 1 and incumbents[0].customer_id == customer_id:
        incumbent = incumbents[0]
        # KAN-84: a re-confirmed row written before KAN-84 has no label yet;
        # the response is built from it, so label it now.
        if incumbent.vehicle_label_refreshed_at is None:
            _write_vehicle_labels(db, [incumbent])
            db.commit()
        return incumbent

    party = _open_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=customer_id, role=role, group_id=group_id,
        actor_id=actor_id, effective_from=effective_from, effective_to=effective_to, incumbents=incumbents,
    )
    db.commit()
    db.refresh(party)
    return party


def _open_holders_in_group(
    db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID,
    excluding: uuid.UUID | None = None,
) -> list[VehicleParty]:
    """Every row holding (vehicle, role) now in `group_id`, oldest first,
    locked FOR UPDATE — found through Customer.group_id (KAN-99), never
    another group's. The caller holds the (vehicle, role, group) advisory
    lock, so no holder can be inserted behind its back. `excluding` leaves
    out the row a reopen is about to make the holder (KAN-148)."""

    stmt = (
        select(VehicleParty)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(
            VehicleParty.vehicle_id == vehicle_id,
            VehicleParty.role == role,
            Customer.group_id == group_id,
            or_(VehicleParty.effective_to.is_(None), VehicleParty.effective_to > utcnow()),
        )
        .order_by(VehicleParty.effective_from)
        .with_for_update(of=VehicleParty)
    )
    if excluding is not None:
        stmt = stmt.where(VehicleParty.id != excluding)
    return list(db.scalars(stmt).all())


def _refuse_merged_customer(db: Session, customer_id: uuid.UUID) -> None:
    """KAN-365: allocation and a reopen refuse a merged customer — the
    survivor carries its vehicle history (ADR-064, KAN-177), and an open row
    left on the merged record is invisible on the survivor's 360. A create
    with a past `effectiveTo` refuses it the same way (KAN-370). The caller
    holds the (vehicle, role, group) advisory lock; the customer row is read
    FOR SHARE under it, so an allocation racing the merge of this customer
    either commits before the merge reads the duplicate's pairs (the merge
    locks the row FOR NO KEY UPDATE first, see _repoint_vehicle_parties) or
    waits for the merge and reads the committed `merged` status here."""

    status, merged_into = db.execute(
        select(Customer.lifecycle_status, Customer.duplicate_of_customer_id)
        .where(Customer.id == customer_id)
        .with_for_update(read=True)
    ).one()
    if status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "This customer has been merged into another; give the vehicle role to the customer it was merged into.",
            details={
                "reason": "customer_merged",
                "mergedIntoCustomerId": str(merged_into) if merged_into else None,
            },
        )


def _lock_vehicle_role_in_group(
    db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID
) -> None:
    """pg_advisory_xact_lock on a 64-bit key derived from (vehicle, role,
    group); released by the transaction's commit or rollback. SQLite (the
    fast local lane, ADR-011) has no advisory locks and serialises
    writers on its own, so this is a no-op there.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _vehicle_role_lock_key(vehicle_id, role, group_id)})


def _try_lock_vehicle_role_in_group(
    db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID
) -> bool:
    """The same lock, without waiting: True once held, False if another
    transaction holds it (KAN-356: a merge's out-of-order late pair). Always
    True on SQLite, as above."""

    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _vehicle_role_lock_key(vehicle_id, role, group_id)}
        ).scalar()
    )


def _vehicle_role_lock_key(vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"vehicle_party:{vehicle_id}:{role.value}:{group_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _open_vehicle_party(
    db: Session, *, vehicle_id: uuid.UUID, customer_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID,
    actor_id: uuid.UUID, effective_from: dt.datetime, effective_to: dt.datetime | None,
    incumbents: list[VehicleParty],
) -> VehicleParty:
    for incumbent in incumbents:
        _close_vehicle_party(db, party=incumbent, actor_id=actor_id, group_id=group_id)

    party = VehicleParty(
        vehicle_id=vehicle_id, customer_id=customer_id, role=role, effective_from=effective_from,
        effective_to=effective_to,
    )
    _write_vehicle_labels(db, [party])
    db.add(party)
    try:
        db.flush()
    except IntegrityError as exc:
        # uq_vehicle_party_scope: this customer already has a row for this
        # role starting at exactly this effectiveFrom (a client-sent date).
        db.rollback()
        raise ConflictError(
            "This customer already has this role on this vehicle as of this date.",
            details={
                "reason": "vehicle_party_exists",
                "vehicleId": str(vehicle_id),
                "role": role.value,
                "effectiveFrom": effective_from.isoformat(),
            },
        ) from exc

    after: dict[str, Any] = {"vehicleId": str(vehicle_id), "role": role.value, "effectiveFrom": effective_from.isoformat()}
    if effective_to is not None:
        after["effectiveTo"] = effective_to.isoformat()
    record_audit_event(
        db, entity_type="customer", entity_id=customer_id, tenant_id=group_id, action="vehicle_party_add",
        actor_id=actor_id, after=after,
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="customer.vehicle_party.linked",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="vehicle_party",
            aggregate_id=party.id,
            payload=_vehicle_party_payload(party),
        ),
    )
    return party


def _vehicle_party_payload(party: VehicleParty) -> dict[str, Any]:
    return {
        "vehiclePartyId": str(party.id),
        "vehicleId": str(party.vehicle_id),
        "customerId": str(party.customer_id),
        "role": party.role.value,
        "effectiveFrom": party.effective_from.isoformat(),
        "effectiveTo": party.effective_to.isoformat() if party.effective_to else None,
        # KAN-177: set only on a copy a customer merge kept closed.
        "mergedIntoCustomerId": str(party.merged_into_customer_id) if party.merged_into_customer_id else None,
    }


def repoint_vehicle_party(db: Session, *, duplicate_vehicle_id: uuid.UUID, survivor_vehicle_id: uuid.UUID) -> int:
    """Called from app.vehicle's merge flow (FR-V-12) via app.customer.public
    — customer stays the sole writer of VehicleParty.vehicle_id even though
    the merge decision belongs to vehicle. ADR-047: a write spanning two
    contexts is a call with a compensating action, never a shared
    transaction — unlike app.sales.services.transaction.
    repoint_customer_transactions (which joins the caller's transaction and
    predates this rule being written down), this function commits its OWN
    transaction and is called AFTER the vehicle side has already committed
    its own repointing. A failure here is repaired by the nightly
    reconciliation job, not by rolling back the vehicle merge — the correct
    trade per ADR-047, not an oversight.
    """

    rows = list(db.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == duplicate_vehicle_id)).all())
    for party in rows:
        party.vehicle_id = survivor_vehicle_id
    # KAN-84: the label follows the vehicle id — the survivor's, not the
    # duplicate's (which may differ in VIN, number and catalogue match).
    _write_vehicle_labels(db, rows)
    db.commit()
    return len(rows)


def set_credit_block(
    db: Session, *, customer: Customer, blocked: bool, reason: str | None, actor_id: uuid.UUID
) -> Customer:
    """WP-8 PR-6 (ADR-065/S-D19) — stops a CONTRACT from being confirmed,
    never an offer (quoting a blocked customer is often how the block gets
    resolved). A reason is required when blocking, matching the codebase's
    "disabled-with-explanation, never bare" posture elsewhere.
    """

    if blocked and not reason:
        raise ConflictError("A reason is required to place a credit block.")

    before = {"creditBlock": customer.credit_block, "creditBlockReason": customer.credit_block_reason}
    customer.credit_block = blocked
    customer.credit_block_reason = reason if blocked else None
    customer.credit_blocked_at = utcnow() if blocked else None
    customer.updated_by = actor_id
    customer.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type="customer",
        entity_id=customer.id,
        tenant_id=customer.group_id,
        action="credit_block_changed",
        actor_id=actor_id,
        before=before,
        after={"creditBlock": customer.credit_block, "creditBlockReason": customer.credit_block_reason},
    )
    db.commit()
    db.refresh(customer)
    return customer


# KAN-371 (Anto, 2026-10-18, option A; ADR-093): the History tab's ids are
# named on read — the audit log is append-only and keeps only the ids. The
# keys whose values are ids (or lists of ids) of each kind, as the customer
# service records them; snake_case keys are read as their camelCase name.
_HISTORY_DEALERSHIP_KEYS = frozenset({"dealershipId"})
_HISTORY_USER_KEYS = frozenset({"advisorId"})
_HISTORY_CUSTOMER_KEYS = frozenset(
    {"mergedIntoCustomerId", "duplicateOfCustomerId", "customerId", "earlierMergesRepointedIds"}
)
_HISTORY_VEHICLE_KEYS = frozenset({"vehicleId"})
_HISTORY_PARTY_KEYS = frozenset({"vehiclePartiesKeptClosedIds"})


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except ValueError:
        return None


def _vehicle_label(make: str | None, model: str | None, model_year: int | None, number: str | None) -> str | None:
    if make and model:
        return f"{make} {model} ({model_year})" if model_year else f"{make} {model}"
    return number


def history_labels(
    db: Session, *, group_id: uuid.UUID, dealership_id: uuid.UUID, events: list[Any]
) -> dict[str, str]:
    """Id → display label for every id the customer's audit events carry
    under a known key (one value or a list of them), and for each event's
    actor: a Betrieb's and a user's name from platform
    (`get_group_display_labels`, ADR-093 — the caller's own Betrieb, a sister
    Betrieb only while the group's read is enabled), another customer's name
    and number, a vehicle's make and model, and a party row by its vehicle,
    both from the party rows' own labels (KAN-84). Only this group's records
    are named; an id that resolves to nothing is left out and the screen
    renders it as unknown. One query per kind, whatever the number of events."""

    ids: dict[str, set[uuid.UUID]] = {kind: set() for kind in ("dealership", "user", "customer", "vehicle", "party")}
    kinds = (
        (_HISTORY_DEALERSHIP_KEYS, "dealership"),
        (_HISTORY_USER_KEYS, "user"),
        (_HISTORY_CUSTOMER_KEYS, "customer"),
        (_HISTORY_VEHICLE_KEYS, "vehicle"),
        (_HISTORY_PARTY_KEYS, "party"),
    )
    for event in events:
        if event.actor_id is not None:
            ids["user"].add(event.actor_id)
        for snapshot in (event.before, event.after):
            for raw_key, value in (snapshot or {}).items():
                head, *rest = raw_key.split("_")
                key = head + "".join(part.title() for part in rest)
                kind = next((name for keys, name in kinds if key in keys), None)
                if kind is None:
                    continue
                for item in value if isinstance(value, list) else [value]:
                    parsed = _uuid_or_none(item) if isinstance(item, str) else None
                    if parsed is not None:
                        ids[kind].add(parsed)

    dealerships, users = get_group_display_labels(
        db, group_id=group_id, dealership_id=dealership_id, dealership_ids=ids["dealership"], user_ids=ids["user"]
    )
    labels: dict[str, str] = {str(k): v for k, v in {**dealerships, **users}.items()}
    if ids["customer"]:
        for customer in db.scalars(
            select(Customer).where(Customer.id.in_(ids["customer"]), Customer.group_id == group_id)
        ):
            labels[str(customer.id)] = f"{customer_display_name(customer)} ({customer.customer_number})"
    party_columns = (
        VehicleParty.vehicle_make,
        VehicleParty.vehicle_model,
        VehicleParty.vehicle_model_year,
        VehicleParty.vehicle_number,
    )
    if ids["vehicle"]:
        for vehicle_id, *label_parts in db.execute(
            select(VehicleParty.vehicle_id, *party_columns)
            .join(Customer, Customer.id == VehicleParty.customer_id)
            .where(VehicleParty.vehicle_id.in_(ids["vehicle"]), Customer.group_id == group_id)
        ):
            label = _vehicle_label(*label_parts)
            if label and (label_parts[0] or str(vehicle_id) not in labels):
                labels[str(vehicle_id)] = label
    if ids["party"]:
        for party_id, *label_parts in db.execute(
            select(VehicleParty.id, *party_columns)
            .join(Customer, Customer.id == VehicleParty.customer_id)
            .where(VehicleParty.id.in_(ids["party"]), Customer.group_id == group_id)
        ):
            label = _vehicle_label(*label_parts)
            if label:
                labels[str(party_id)] = label
    return labels
