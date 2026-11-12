"""CustomerPhone / CustomerEmail — multi-valued contact details with consent per
channel (ADR-067; split from customer.py, KAN-617). One primary per
type-group, settled by _prepare_primary_change; FR-03's last-contact-point
guard.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.validators import normalise_phone
from app.customer.models.customer import (
    ConsentScope,
    Customer,
    CustomerEmail,
    CustomerPhone,
)
from app.customer.schemas.customer import (
    CustomerEmailCreate,
    CustomerEmailUpdate,
    CustomerPhoneCreate,
    CustomerPhoneUpdate,
)
from app.customer.services.customer_common import (
    _CONTACT_TYPE_COLUMN,
    _fixup_single_primary,
    _group_scoped_or_404,
    _is_usable_row,
    _plain,
)

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
            "This phone number is already on this customer.",
            details={"reason": "phone_already_on_customer", "phoneE164": data.phone_e164},
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
    groups_to_settle = _prepare_primary_change(db, CustomerPhone, row=phone, changes=changes, noun="phone")

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
            "This phone number is already on this customer.",
            details={"reason": "phone_already_on_customer", "phoneE164": changes.get("phone_e164")},
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
            " email address.",
            details={"reason": "last_usable_contact_point"},
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
            "This email address is already on this customer.",
            details={"reason": "email_already_on_customer", "emailAddress": data.email_address},
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
    groups_to_settle = _prepare_primary_change(db, CustomerEmail, row=email, changes=changes, noun="email")

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
            details={"reason": "email_already_on_customer", "emailAddress": changes.get("email_address")},
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


def _prepare_primary_change(db: Session, model: type, *, row: Any, changes: dict[str, Any], noun: str) -> list[Any]:
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
    PATCH says `isPrimary: true`, which then wins as an explicit request and
    demotes that primary (Anto, 2026-11-11, KAN-349, option A; pinned for
    phone, email and address in tests/test_customer_contact_channels.py).
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
            f"Cannot unset the primary {noun} directly — mark a different {noun} as primary instead.",
            details={"reason": "primary_unset_directly"},
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
