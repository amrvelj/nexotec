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

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.core.postal_codes import derive_canton
from app.core.redact import REDACTED_PLACEHOLDER, is_secret_field
from app.core.validators import normalise_phone
from app.customer.models.customer import (
    AddressType,
    Customer,
    CustomerAddress,
    CustomerEmail,
    CustomerLifecycleStatus,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerTag,
    CustomerType,
)
from app.customer.models.vehicle_party import VehicleParty
from app.customer.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
)
from app.customer.services.contact_channels import _consent_create_kwargs
from app.customer.services.customer_common import (
    _EVENT_PRODUCER,
    _customer_label_payload,
    _is_usable_row,
    _plain,
    _validate_country_codes,
    _violated_constraint,
    customer_display_name,
)
from app.platform.public import (
    get_active_user_acting_in,
    get_group_display_labels,
    list_active_users_acting_in,
)

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
_TERMINAL_LIFECYCLE_STATUSES = {CustomerLifecycleStatus.MERGED}


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


def _redact(field: str, value: Any) -> Any:
    if is_secret_field(field) and value is not None:
        return REDACTED_PLACEHOLDER
    return _plain(value)


_INDIVIDUAL_ONLY_FIELDS = {"first_name", "last_name", "birth_date", "nationality", "title", "gender"}
_BUSINESS_ONLY_FIELDS = {"company_name", "legal_form", "tax_id"}


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
