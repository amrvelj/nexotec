"""Customer service layer: GROUP-scoped CRUD (WP-3 PR-2, ADR-014 — moved
from tenant-scoped), duplicate-check typeahead, and merge, all operating
group-wide. PII changes (name/email/phone/address) and lifecycle_status
transitions are audit-logged with before/after (spec: "Every change to PII
... is audit-logged"; lifecycle_status added for the same accountability
reason Dealership/User audit their status fields).

`app.core.audit.record_audit_event`/`app.core.outbox.OutboxEvent` keep their
own generic `tenant_id` keyword — that's a cross-cutting parameter name
shared by every context, not something this one PR renames everywhere — but
every call site here now passes `contact.group_id` as its value, since
that's Contact's real scoping key.
"""

import uuid
from typing import Any

from sqlalchemy import not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    AddressType,
    Contact,
    ContactAddress,
    ContactEmail,
    ContactLifecycleStatus,
    ContactNumberSequence,
    ContactPhone,
    ContactRole,
    ContactTag,
    ContactType,
)
from app.contact.models.vehicle_party import VehicleParty
from app.contact.schemas.contact import (
    ContactCreate,
    ContactUpdate,
)
from app.contact.services import contact_groups as contact_groups_service
from app.contact.services import roles as roles_service
from app.contact.services.contact_channels import _consent_create_kwargs
from app.contact.services.contact_common import (
    _EVENT_PRODUCER,
    CONTACT_AUDIT_ENTITY_TYPES,
    _contact_label_payload,
    _is_usable_row,
    _plain,
    _validate_country_codes,
    _violated_constraint,
    contact_display_name,
)
from app.core.audit import record_audit_event
from app.core.audit_attribution import attributed
from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import BadRequestError, ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event
from app.core.postal_codes import derive_canton
from app.core.redact import REDACTED_PLACEHOLDER, is_secret_field
from app.core.validators import normalise_phone
from app.platform.public import (
    get_active_user_acting_in,
    get_group_display_labels,
    get_offboarded_audit_attribution,
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
# contact_number and language are audited too: the number because it is
# the immutable business key printed on documents, language because sending
# a contact a contract in the wrong language is exactly the kind of change
# someone later disputes. The FR-17 commercial-standing and relationship
# fields (KAN-50) are audited but are not all PII.
_AUDITED_FIELDS = _PII_FIELDS | {
    "contact_number",
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
    "contact_since",
    "next_follow_up",
    "dealership_id",
}
_TERMINAL_LIFECYCLE_STATUSES = {ContactLifecycleStatus.MERGED}


def _default_primary_flags(items: list[Any], type_of) -> list[bool]:
    """Per contact-type-group, not per contact (ADR-067): if the caller
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
    sister-Betrieb colleague who works here advises the contacts they take
    on here), and active or invited. A suspended or deactivated user advises
    no contact, since the deactivation step hands a leaver's contacts over
    (FR-A-37, D-A-35) and a write must not put them back.
    """

    user = get_active_user_acting_in(db, dealership_id=dealership_id, user_id=user_id)
    if user is None:
        raise UnprocessableEntityError(
            "advisorId is not an active user of your dealership.", details={"advisorId": str(user_id)}
        )
    return user.label


def list_advisor_options(db: Session, *, dealership_id: uuid.UUID) -> list[dict[str, Any]]:
    """Everyone `_resolve_advisor_label` admits for the acting dealership —
    its active and invited users and its active members (KAN-410) — as
    advisor-picker options (KAN-50). Same `First Last` label that lands in
    Contact.advisor_label."""

    return [
        {"id": user.id, "label": user.label}
        for user in list_active_users_acting_in(db, dealership_id=dealership_id)
    ]


def _apply_advisor(contact: Contact, *, advisor_id: uuid.UUID | None, label: str | None) -> None:
    contact.advisor_id = advisor_id
    contact.advisor_label = label
    contact.advisor_label_refreshed_at = utcnow() if advisor_id is not None else None


def _replace_contact_tags(db: Session, contact: Contact, tags: list[str], *, actor_id: uuid.UUID) -> None:
    """Reconcile contact_tag rows to exactly `tags` (already normalised by
    the schema): insert the missing, delete the absent. One local
    transaction; the caller commits.
    """

    existing = {row.tag: row for row in db.scalars(select(ContactTag).where(ContactTag.contact_id == contact.id)).all()}
    wanted = set(tags)
    for tag in wanted - existing.keys():
        db.add(
            ContactTag(
                group_id=contact.group_id,
                contact_id=contact.id,
                tag=tag,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
    for tag, row in existing.items():
        if tag not in wanted:
            db.delete(row)


def _contact_tags(db: Session, contact_id: uuid.UUID) -> list[str]:
    return sorted(db.scalars(select(ContactTag.tag).where(ContactTag.contact_id == contact_id)).all())


def _validate_contact_type_fields_on_update(contact: Contact, changes: dict[str, Any]) -> None:
    """contact_type is immutable and not part of ContactUpdate (see schema
    docstring) — check any individual/business-only fields present in this
    PATCH against the contact's existing, unchanging type.
    """

    forbidden = _BUSINESS_ONLY_FIELDS if contact.contact_type == ContactType.INDIVIDUAL else _INDIVIDUAL_ONLY_FIELDS
    label = "business-only" if contact.contact_type == ContactType.INDIVIDUAL else "individual-only"
    for field in forbidden:
        if changes.get(field) is not None:
            raise BadRequestError(
                f"'{field}' is a {label} field and cannot be set on a {contact.contact_type.value} contact."
            )


def _allocate_contact_number(db: Session, group_id: uuid.UUID) -> str:
    """Allocate the next `K-000001`-style number for this group (D-02, moved
    from per-dealership to per-group in WP-3 PR-2).

    Takes a row lock on the group's counter so two concurrent creates
    serialise here rather than racing to the same number; the lock is held
    until the surrounding contact-creation transaction commits. On SQLite
    (the fast test lane) `with_for_update` is a no-op, which is fine —
    SQLite serialises writers anyway.

    Numbers are allocated, not derived from a COUNT. A failed transaction
    rolls back its increment with everything else, so the next caller is
    re-issued that number (G-71) — it never reached a committed contact.
    What must never happen is reuse of a committed number, because it
    would silently point at two different contacts in printed documents.
    """

    row = db.get(ContactNumberSequence, group_id, with_for_update=True)
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
                db.add(ContactNumberSequence(group_id=group_id, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(ContactNumberSequence, group_id, with_for_update=True)
        assert row is not None, "ContactNumberSequence row missing after its first-use INSERT or the concurrent winner's"

    value = row.next_value
    row.next_value = value + 1
    db.flush()
    return f"K-{value:06d}"


def _add_contacts(db: Session, contact: Contact, data: ContactCreate) -> None:
    """Write the nested phones/emails/addresses from a create request.

    Runs inside the caller's transaction, so a contact and its contact
    details commit together or not at all — there is no window in which a
    contact exists while violating its own "at least one contact point"
    invariant (FR-03).

    is_primary defaulting is per (type) within each list — see
    _default_primary_flags — since ADR-067 scopes "exactly one primary" to
    the type-group, not the whole contact.
    """

    for phone, is_default_primary in zip(data.phones, _default_primary_flags(data.phones, lambda p: p.phone_type)):
        db.add(
            ContactPhone(
                group_id=contact.group_id,
                contact_id=contact.id,
                phone_type=phone.phone_type,
                label=phone.label,
                phone_e164=phone.phone_e164,
                phone_normalised=normalise_phone(phone.phone_e164),
                is_primary=phone.is_primary or is_default_primary,
                created_by=contact.created_by,
                updated_by=contact.updated_by,
                **_consent_create_kwargs(phone),
            )
        )
    for email, is_default_primary in zip(data.emails, _default_primary_flags(data.emails, lambda e: e.email_type)):
        db.add(
            ContactEmail(
                group_id=contact.group_id,
                contact_id=contact.id,
                email_type=email.email_type,
                label=email.label,
                email_address=email.email_address,
                is_primary=email.is_primary or is_default_primary,
                created_by=contact.created_by,
                updated_by=contact.updated_by,
                **_consent_create_kwargs(email),
            )
        )
    for address, is_default_primary in zip(
        data.addresses, _default_primary_flags(data.addresses, lambda a: a.address_type)
    ):
        db.add(
            ContactAddress(
                group_id=contact.group_id,
                contact_id=contact.id,
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
                created_by=contact.created_by,
                updated_by=contact.updated_by,
                **_consent_create_kwargs(address),
            )
        )


def create_contact(
    db: Session,
    *,
    group_id: uuid.UUID,
    data: ContactCreate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    dealership_id: uuid.UUID,
    may_write_customer_role: bool = True,
) -> Contact:
    # KAN-464 Q3: only who may write the customer role gives it
    # (services/roles.py). The API passes the principal's matrix cell.
    if ContactRole.CUSTOMER in data.roles and not may_write_customer_role:
        raise roles_service.customer_role_not_permitted("roles")
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
    # KAN-464: a contact created without the customer role carries no
    # customer field (Q1, services/roles.py).
    lifecycle_status = roles_service.check_create(
        data.roles, fields_set=data.model_fields_set, lifecycle_status=data.lifecycle_status, advisor_id=data.advisor_id
    )
    holds_customer = ContactRole.CUSTOMER in data.roles
    contact = Contact(
        group_id=group_id,
        contact_number=_allocate_contact_number(db, group_id),
        contact_type=data.contact_type,
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
        lifecycle_status=lifecycle_status,
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
        contact_since=data.contact_since,
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
    # contact unassigned rather than failing the whole create.
    # D-24 is a customer rule: a supplier-only contact gets no advisor.
    advisor_id = data.advisor_id if data.advisor_id is not None else (actor_id if holds_customer else None)
    if advisor_id is not None:
        try:
            _apply_advisor(
                contact,
                advisor_id=advisor_id,
                label=_resolve_advisor_label(db, dealership_id=dealership_id, user_id=advisor_id),
            )
        except UnprocessableEntityError:
            if data.advisor_id is not None:
                raise
            _apply_advisor(contact, advisor_id=None, label=None)

    db.add(contact)
    db.flush()
    _add_contacts(db, contact, data)
    _replace_contact_tags(db, contact, data.tags, actor_id=actor_id)
    contact_groups_service.check_contact_group_change(
        db, dealership_id=dealership_id, current=set(), wanted=set(data.contact_groups)
    )
    contact_groups_service.replace_contact_group_assignments(db, contact, data.contact_groups, actor_id=actor_id)
    roles_service.replace_roles(db, contact, data.roles, actor_id=actor_id)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # No longer an email-uniqueness failure: the tenant-wide unique
        # constraint on email was dropped in Phase B (D-05), because family
        # members and colleagues legitimately share an address. What can
        # still collide here is the same number/address supplied twice for
        # one contact, or a contact_number race.
        raise ConflictError("Contact contact details conflict with an existing record.") from exc

    after = {field: _redact(field, getattr(contact, field)) for field in _AUDITED_FIELDS}
    after["tags"] = sorted(data.tags)
    after[contact_groups_service.AUDIT_FIELD] = sorted(data.contact_groups)
    after[roles_service.AUDIT_FIELD] = [role.value for role in data.roles]
    record_audit_event(
        db,
        entity_type="contact",
        entity_id=contact.id,
        tenant_id=group_id,
        action="create",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        after=after,
    )
    publish_event(
        db,
        OutboxEvent(
            event_type="contact.created",
            tenant_id=group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="contact",
            aggregate_id=contact.id,
            payload=_contact_label_payload(contact),
        ),
    )
    db.commit()
    db.refresh(contact)
    return contact


def _lock_for_role_change(db: Session, contact: Contact) -> None:
    """KAN-464: a change of roles and a credit block judge each other (the
    block is the customer role's), so both take the contact row FOR UPDATE
    and re-read it: the second waits for the first and sees its outcome. A
    write that committed in between (another version) is a 409, as the
    If-Match check would have answered."""

    version_read = contact.version
    db.execute(select(Contact.id).where(Contact.id == contact.id).with_for_update())
    db.refresh(contact)
    check_version(contact.version, version_read, entity_name="Contact")


def update_contact(
    db: Session,
    *,
    contact: Contact,
    data: ContactUpdate,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    dealership_id: uuid.UUID,
    may_write_customer_role: bool = True,
) -> Contact:
    if contact.lifecycle_status in _TERMINAL_LIFECYCLE_STATUSES:
        raise ConflictError(
            f"Contact lifecycle_status '{contact.lifecycle_status.value}' is terminal and cannot be changed"
            " via PATCH.",
            details={"currentLifecycleStatus": contact.lifecycle_status.value},
        )

    changes = data.model_dump(exclude_unset=True)
    _validate_contact_type_fields_on_update(contact, changes)
    if "nationality" in changes:
        _validate_country_codes(db, {"nationality": changes["nationality"]})

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    # KAN-464: the roles first — taking the customer role away closes the
    # customer fields in this same write (services/roles.py), so the advisor
    # and lifecycle handling below see the closing in `changes`.
    if changes.keys() & roles_service.CUSTOMER_ROLE_FIELDS:
        # A PATCH setting a customer field is judged against the roles as
        # they are under the lock, so it cannot slip past one taking the
        # customer role away (or the other way round).
        _lock_for_role_change(db, contact)
    current_roles = roles_service.open_roles(db, contact.id)
    new_roles = [role.value for role in changes.pop("roles")] if "roles" in changes else current_roles
    # KAN-464 Q3: judged under the same lock as the role rules — every
    # field it judges is in CUSTOMER_ROLE_FIELDS.
    roles_service.check_customer_role_write_on_update(
        contact,
        may_write_customer_role=may_write_customer_role,
        held_customer_before=ContactRole.CUSTOMER.value in current_roles,
        holds_customer_after=ContactRole.CUSTOMER.value in new_roles,
        changes=changes,
    )
    roles_service.apply_role_rules_on_update(
        contact,
        holds_customer_after=ContactRole.CUSTOMER.value in new_roles,
        held_customer_before=ContactRole.CUSTOMER.value in current_roles,
        changes=changes,
    )
    if new_roles != current_roles:
        before[roles_service.AUDIT_FIELD] = current_roles
        after[roles_service.AUDIT_FIELD] = new_roles
        roles_service.replace_roles(db, contact, [ContactRole(role) for role in new_roles], actor_id=actor_id)

    # advisor_id is handled out of the generic loop — it drives two more
    # denormalised columns and (D-24) is never re-defaulted on update: an
    # explicit id resolves a fresh label, an explicit null clears all three.
    if "advisor_id" in changes:
        new_advisor_id = changes.pop("advisor_id")
        if contact.advisor_id != new_advisor_id:
            before["advisor_id"] = _redact("advisor_id", contact.advisor_id)
            after["advisor_id"] = _redact("advisor_id", new_advisor_id)
            if new_advisor_id is None:
                _apply_advisor(contact, advisor_id=None, label=None)
            else:
                _apply_advisor(
                    contact,
                    advisor_id=new_advisor_id,
                    label=_resolve_advisor_label(db, dealership_id=dealership_id, user_id=new_advisor_id),
                )

    # tags live in a child table — full-replace against the normalised set.
    if "tags" in changes:
        new_tags = sorted(changes.pop("tags"))
        current_tags = _contact_tags(db, contact.id)
        if new_tags != current_tags:
            before["tags"] = current_tags
            after["tags"] = new_tags
            _replace_contact_tags(db, contact, new_tags, actor_id=actor_id)

    # KAN-466: contact groups, full replace like tags; what the acting
    # Betrieb may not set is read-only from here (services/contact_groups.py).
    if "contact_groups" in changes:
        new_groups = sorted(changes.pop("contact_groups"))
        current_groups = contact_groups_service.contact_group_assignments(db, contact.id)
        if new_groups != current_groups:
            contact_groups_service.check_contact_group_change(
                db, dealership_id=dealership_id, current=set(current_groups), wanted=set(new_groups)
            )
            before[contact_groups_service.AUDIT_FIELD] = current_groups
            after[contact_groups_service.AUDIT_FIELD] = new_groups
            contact_groups_service.replace_contact_group_assignments(db, contact, new_groups, actor_id=actor_id)

    for field, value in changes.items():
        current = getattr(contact, field)
        if current == value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _redact(field, current)
            after[field] = _redact(field, value)
        setattr(contact, field, value)

    contact.updated_by = actor_id
    contact.version += 1

    # KAN-390: the contact row and its new tag rows reach the database here,
    # inside the guard — not at the audit row's flush below, outside it. A
    # merge into this contact (KAN-372 holds it FOR SHARE) can move a
    # duplicate's tag of the same name onto it while this PATCH waits; the
    # insert then hits uq_contact_tag_contact_id_tag, a conflict (409), not
    # a 500. Tags, roles and contact groups (KAN-466: the same race on
    # uq_contact_group_assignment_contact_id_value_code, 409
    # contact_group_assignment_exists) are this function's only child rows. The
    # PATCH waits before inserting any of them because the flush writes
    # `contact` before `contact_group_assignment`, `contact_role` (KAN-464:
    # the same race on uq_contact_role_contact_id_role_open, 409
    # contact_role_exists) and `contact_tag` —
    # SQLAlchemy orders these unrelated mappers by name, not by a
    # relationship — so it never holds a child row the merge needs.
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        violated = _violated_constraint(exc)
        if violated == roles_service.OPEN_ROLE_CONSTRAINT:
            # The same race (KAN-390): a merge gave this contact the role
            # while the PATCH waited.
            raise ConflictError(
                "A role in this change is already held by the contact: it changed in the meantime (a contact may"
                " have been merged into it). Reload it and try again.",
                details={"reason": "contact_role_exists"},
            ) from exc
        if violated == "uq_contact_group_assignment_contact_id_value_code":
            # The same race as a tag's (KAN-390): a merge moved a duplicate's
            # group onto this contact while the PATCH waited.
            raise ConflictError(
                "A contact group in this change is already on the contact: it changed in the meantime (a contact"
                " may have been merged into it). Reload it and try again.",
                details={"reason": "contact_group_assignment_exists"},
            ) from exc
        if violated != "uq_contact_tag_contact_id_tag":
            raise ConflictError("This change conflicts with an existing record.") from exc
        raise ConflictError(
            "A tag in this change is already on the contact: it changed in the meantime (a contact may have"
            " been merged into it). Reload it and try again.",
            details={"reason": "contact_tag_exists"},
        ) from exc

    if before or after:
        record_audit_event(
            db,
            entity_type="contact",
            entity_id=contact.id,
            tenant_id=contact.group_id,
            action="update",
            actor_id=actor_id,
            acting_dealership_id=acting_dealership_id,
            before=before or None,
            after=after or None,
        )
        publish_event(
            db,
            OutboxEvent(
                event_type="contact.updated",
                tenant_id=contact.group_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="contact",
                aggregate_id=contact.id,
                payload=_contact_label_payload(contact),
            ),
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("This change conflicts with an existing record.") from exc
    db.refresh(contact)
    return contact


def has_usable_domicile_address(db: Session, *, contact_id: uuid.UUID) -> bool:
    """Whether the contact has at least one usable `domicile` address row
    — `valid_to` unset (a future-dated close counts as closed) and not
    `do_not_use`, via the shared `_is_usable_row`.

    This is exactly `ContactRead.address is not None`: the `address`
    projection resolves the primary-flagged usable domicile row, and
    `_primary_of_type` falls back to the oldest usable domicile row when
    none is flagged — so both this predicate and the projection reduce to
    "≥ 1 usable domicile row exists". Keep in step with
    `compute_contact_projections_batch`, not with `_fixup_single_primary`
    (which the equivalence does not depend on).

    A `billing` (or any non-domicile) address does not satisfy this — D-20,
    ruled 2026-09-07: the Kaufvertrag identifies the buyer by domicile, and
    the billing-address question belongs to WP-9 invoicing.

    Exposed through `contact.public` for Sales' contract-confirmation gate
    (D-20 / KAN-55): a contract cannot be confirmed for a contact whose
    address the dealership does not have. An offer is never blocked.
    """

    rows = db.scalars(
        select(ContactAddress).where(
            ContactAddress.contact_id == contact_id,
            ContactAddress.address_type == AddressType.DOMICILE,
        )
    ).all()
    return any(_is_usable_row(row) for row in rows)


def set_credit_block(
    db: Session,
    *,
    contact: Contact,
    blocked: bool,
    reason: str | None,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    may_write_customer_role: bool = True,
) -> Contact:
    """WP-8 PR-6 (ADR-065/S-D19) — stops a CONTRACT from being confirmed,
    never an offer (quoting a blocked contact is often how the block gets
    resolved). A reason is required when blocking, matching the codebase's
    "disabled-with-explanation, never bare" posture elsewhere.
    """

    # KAN-464 Q3: the credit block is the customer role's data — placing
    # and lifting it alike.
    if not may_write_customer_role:
        raise roles_service.customer_role_not_permitted("creditBlock")
    if blocked and not reason:
        raise ConflictError("A reason is required to place a credit block.")
    if blocked:
        # KAN-464 (Q1): the credit block is the customer role's. Locked
        # first, so a PATCH taking the role away cannot slip in between.
        _lock_for_role_change(db, contact)
        if ContactRole.CUSTOMER.value not in roles_service.open_roles(db, contact.id):
            raise roles_service.customer_role_required("creditBlock")

    before = {"creditBlock": contact.credit_block, "creditBlockReason": contact.credit_block_reason}
    contact.credit_block = blocked
    contact.credit_block_reason = reason if blocked else None
    contact.credit_blocked_at = utcnow() if blocked else None
    contact.updated_by = actor_id
    contact.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type="contact",
        entity_id=contact.id,
        tenant_id=contact.group_id,
        action="credit_block_changed",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before=before,
        after={"creditBlock": contact.credit_block, "creditBlockReason": contact.credit_block_reason},
    )
    db.commit()
    db.refresh(contact)
    return contact


# KAN-371 (Anto, 2026-10-18, option A; ADR-093): the History tab's ids are
# named on read — the audit log is append-only and keeps only the ids. The
# keys whose values are ids (or lists of ids) of each kind, as the contact
# service records them; snake_case keys are read as their camelCase name.
_HISTORY_DEALERSHIP_KEYS = frozenset({"dealershipId"})
_HISTORY_USER_KEYS = frozenset({"advisorId"})
_HISTORY_CONTACT_KEYS = frozenset(
    {
        "mergedIntoContactId",
        "duplicateOfContactId",
        "contactId",
        "earlierMergesRepointedIds",
        # rows written before the rename to contacts (KAN-464 PR-3)
        "mergedIntoCustomerId",
        "duplicateOfCustomerId",
        "customerId",
    }
)
# KAN-680: a vehicle merge's kept copy names the record it was merged into.
_HISTORY_VEHICLE_KEYS = frozenset({"vehicleId", "mergedIntoVehicleId"})
_HISTORY_PARTY_KEYS = frozenset({"vehiclePartiesKeptClosedIds", "vehiclePartyCopiesRepointedIds"})


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except ValueError:
        return None


def _vehicle_label(make: str | None, model: str | None, model_year: int | None, number: str | None) -> str | None:
    if make and model:
        return f"{make} {model} ({model_year})" if model_year else f"{make} {model}"
    return number


def list_contact_history(db: Session, *, group_id: uuid.UUID, contact_id: uuid.UUID) -> list[AuditEvent]:
    """The History tab of a group contact: its audit rows, oldest first,
    without the rows an offboarded Betrieb of the group wrote (KAN-722;
    KAN-218 Q2 A, «leaves every group read»). Which rows those are is the
    one rule the leaver's archive export uses to take them
    (`app.core.audit_attribution`), so what its sisters stop seeing is what
    its archive holds. The caller has found the contact in its own group."""

    hidden = get_offboarded_audit_attribution(db, group_id=group_id)
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.entity_type.in_(CONTACT_AUDIT_ENTITY_TYPES),
            AuditEvent.entity_id == contact_id,
            AuditEvent.tenant_id == group_id,
            not_(attributed(AuditEvent, hidden)),
        )
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
    )
    return list(db.scalars(stmt).all())


def history_labels(
    db: Session, *, group_id: uuid.UUID, dealership_id: uuid.UUID, events: list[Any]
) -> dict[str, str]:
    """Id → display label for every id the contact's audit events carry
    under a known key (one value or a list of them), and for each event's
    actor: a Betrieb's and a user's name from platform
    (`get_group_display_labels`, ADR-093 — the caller's own Betrieb, a sister
    Betrieb only while the group's read is enabled), another contact's name
    and number, a vehicle's make and model, and a party row by its vehicle,
    both from the party rows' own labels (KAN-84). Only this group's records
    are named; an id that resolves to nothing is left out and the screen
    renders it as unknown. One query per kind, whatever the number of events."""

    ids: dict[str, set[uuid.UUID]] = {kind: set() for kind in ("dealership", "user", "contact", "vehicle", "party")}
    kinds = (
        (_HISTORY_DEALERSHIP_KEYS, "dealership"),
        (_HISTORY_USER_KEYS, "user"),
        (_HISTORY_CONTACT_KEYS, "contact"),
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
    if ids["contact"]:
        for contact in db.scalars(
            select(Contact).where(Contact.id.in_(ids["contact"]), Contact.group_id == group_id)
        ):
            labels[str(contact.id)] = f"{contact_display_name(contact)} ({contact.contact_number})"
    party_columns = (
        VehicleParty.vehicle_make,
        VehicleParty.vehicle_model,
        VehicleParty.vehicle_model_year,
        VehicleParty.vehicle_number,
    )
    if ids["vehicle"]:
        for vehicle_id, *label_parts in db.execute(
            select(VehicleParty.vehicle_id, *party_columns)
            .join(Contact, Contact.id == VehicleParty.contact_id)
            .where(VehicleParty.vehicle_id.in_(ids["vehicle"]), Contact.group_id == group_id)
        ):
            label = _vehicle_label(*label_parts)
            if label and (label_parts[0] or str(vehicle_id) not in labels):
                labels[str(vehicle_id)] = label
    if ids["party"]:
        for party_id, *label_parts in db.execute(
            select(VehicleParty.id, *party_columns)
            .join(Contact, Contact.id == VehicleParty.contact_id)
            .where(VehicleParty.id.in_(ids["party"]), Contact.group_id == group_id)
        ):
            label = _vehicle_label(*label_parts)
            if label:
                labels[str(party_id)] = label
    return labels
