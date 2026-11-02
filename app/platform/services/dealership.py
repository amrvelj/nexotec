"""Dealership service layer: create/read/update + the audit-logging and
lifecycle rules the spec calls out (legal identity — legal name, UID, VAT
number, VAT liability — and status changes are audit-logged, the permission
matrix's "Dealership legal identity" row; `offboarded` is a terminal status).
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import BadRequestError, ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.models.dealership import (
    LEGAL_IDENTITY_FIELDS,
    DealerGroup,
    Dealership,
    DealershipStatus,
    Location,
)
from app.platform.reference_data_seed import ASSOCIATION_MEMBERSHIP_LIST_CODE, LEGAL_FORM_LIST_CODE
from app.platform.schemas.dealership import (
    DealershipAddress,
    DealershipCreate,
    DealershipOverrideAddress,
    DealershipUpdate,
)
from app.platform.services.reference_data import get_active_reference_value_codes

# KAN-203 (enforcement rule 7: every administrative write with before and
# after): every changed column is audited, the address blocks as a whole.
# The UID is public (D-A-22), so nothing is redacted. Before KAN-203 only the
# legal identity and the status were.
_NOT_STORED = {"confirmation", "audit_note"}
_TERMINAL_DEALERSHIP_STATUSES = {DealershipStatus.OFFBOARDED}
_ADDRESS_FIELDS = ("street", "house_number", "postal_code", "locality", "canton", "country")
_ADDRESS_BLOCKS = ("address", "correspondence_address", "billing_address")


def _plain(value: Any) -> Any:
    """A JSON-safe copy of a column value for the audit row: enum members as
    their value, Decimals and dates as strings (KAN-203 audits every field)."""

    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def _apply_address(
    dealership: Dealership, prefix: str, address: DealershipAddress | DealershipOverrideAddress | None
) -> None:
    """Write one address block's six columns, or clear all six (D-A-25: an
    override is whole or absent). The registered seat (`address`) is never
    cleared — its schema field is not nullable."""

    for field in _ADDRESS_FIELDS:
        setattr(dealership, f"{prefix}_{field}", getattr(address, field) if address is not None else None)


def _assert_reference_codes(db: Session, *, list_code: str, label: str, codes: list[str]) -> None:
    """KAN-203: `legalForm` and `associationMemberships` hold codes of the
    platform-seeded lists; an unknown or retired code is a 422, a missing
    list a deployment fault (the country rule, KAN-32)."""

    if not codes:
        return
    valid = get_active_reference_value_codes(db, list_code)
    if valid is None:
        raise RuntimeError(
            f"The '{list_code}' reference list is not seeded. Run `alembic upgrade heads` "
            "(migration e1f7a3c9b2d4) on this deployment."
        )
    unknown = [code for code in codes if code not in valid]
    if unknown:
        raise UnprocessableEntityError(
            f"{label}: {', '.join(repr(c) for c in unknown)} is not an active value of the '{list_code}' list.",
            details={"reason": "unknown_reference_code", "field": label, "codes": unknown},
        )


def _uid_conflict(uid: str | None) -> ConflictError:
    return ConflictError("A dealership with this UID already exists.", details={"uid": uid})


def _assert_vat_number_matches_uid(uid: str | None, vat_number: str | None) -> None:
    """The VAT number is the UID with the MWST / TVA / IVA suffix (D-A-22): a
    VAT number naming another UID is a typo, refused as 422 before it reaches
    a document."""

    if uid is not None and vat_number is not None and vat_number[:15] != uid:
        raise UnprocessableEntityError(
            "vatNumber must be this dealership's own UID followed by MWST, TVA or IVA.",
            details={"uid": uid, "vatNumber": vat_number},
        )


def get_dealer_group_or_404(db: Session, dealer_group_id: uuid.UUID) -> DealerGroup:
    group = db.get(DealerGroup, dealer_group_id)
    if group is None:
        raise NotFoundError(f"DealerGroup {dealer_group_id} was not found.")
    return group


def enable_group_read(db: Session, *, dealer_group: DealerGroup, actor_id: uuid.UUID) -> DealerGroup:
    """ADR-030 (2): platform_admin may flip this ONLY once at least one
    legal_basis row has ever been recorded for the group. This is a
    fail-fast admin UX check, not the actual security boundary — the
    group-read helper itself (app.customer.services.legal_basis.
    get_customer_group_read_or_404) re-checks a LIVE basis per customer on
    every call regardless of this flag, since a basis can be withdrawn
    later without this flag being touched again.
    """

    # Import deferred to call time, not module top-level: app.platform.public
    # is imported (directly or transitively) by nearly every other context
    # at their own module top-level, and app.customer.public itself imports
    # app.customer.services.customer, which imports app.vehicle.public,
    # which imports app.platform.public — a top-level import here would
    # complete that cycle. Both edges are still visible to import-linter,
    # which walks the whole AST, not just top-level imports (same pattern
    # already used by app.customer.services.customer._repoint_transactions).
    from app.customer.public import has_any_basis_for_group

    if not has_any_basis_for_group(db, group_id=dealer_group.id):
        raise BadRequestError(
            "Cannot enable group-read for a group with no legal_basis recorded yet — "
            "record a signed joint-controller agreement first."
        )
    dealer_group.group_read_enabled = True
    dealer_group.updated_by = actor_id
    dealer_group.version += 1
    db.commit()
    db.refresh(dealer_group)
    return dealer_group


def get_dealership_or_404(db: Session, dealership_id: uuid.UUID) -> Dealership:
    dealership = db.get(Dealership, dealership_id)
    if dealership is None:
        raise NotFoundError(f"Dealership {dealership_id} was not found.")
    return dealership


def get_dealership_for_update_or_404(db: Session, dealership_id: uuid.UUID) -> Dealership:
    """The row locked for the rest of the transaction — for a write whose
    If-Match check and version bump must not race another writer (two PUTs of
    the entitlement set would otherwise both pass the check, both write the
    same `entitlements_version`, and leave a merged set neither sent)."""

    dealership = db.scalar(select(Dealership).where(Dealership.id == dealership_id).with_for_update())
    if dealership is None:
        raise NotFoundError(f"Dealership {dealership_id} was not found.")
    return dealership


def get_location_or_404(db: Session, location_id: uuid.UUID) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise NotFoundError(f"Location {location_id} was not found.")
    return location


def get_dealership_default_correspondence_language(db: Session, dealership_id: uuid.UUID) -> SwissLanguage:
    """WP-6b: the "dealership default" half of Customers FR-13's
    correspondence-language rule (document -> customer's language where one
    exists, else this). The "check the customer first" half is each calling
    module's own job — app.platform can't import app.customer to do it
    here, and this function's contract is deliberately narrow: it never
    guesses at a customer, it only ever answers "what does THIS dealership
    fall back to."
    """

    return get_dealership_or_404(db, dealership_id).default_correspondence_language


def list_dealerships_by_ids(db: Session, dealership_ids) -> list[Dealership]:
    """Unordered lookup for building a membership summary list (WP-3 PR-3)
    — the caller re-orders (active dealership first) if it needs a specific
    display order.
    """

    if not dealership_ids:
        return []
    return list(db.scalars(select(Dealership).where(Dealership.id.in_(dealership_ids))).all())


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

    _assert_vat_number_matches_uid(data.uid, data.vat_number)
    dealership = Dealership(
        dealer_group_id=dealer_group_id,
        legal_name=data.legal_name,
        trade_name=data.trade_name,
        franchise_type=data.franchise_type,
        oem_affiliations=data.oem_affiliations,
        phone=data.phone,
        uid=data.uid,
        vat_number=data.vat_number,
        vat_liable=data.vat_liable,
        created_by=actor_id,
        updated_by=actor_id,
    )
    _apply_address(dealership, "address", data.address)
    _apply_address(dealership, "correspondence_address", data.correspondence_address)
    _apply_address(dealership, "billing_address", data.billing_address)
    db.add(dealership)
    try:
        db.flush()
    except IntegrityError as exc:
        # uq_dealership_uid — the duplicate-tenant check D-A-06 asks for.
        db.rollback()
        raise _uid_conflict(data.uid) from exc
    # KAN-203 (FR-A-25): the fiscal defaults exist before anyone opens the
    # Business screen — the shipped values, nothing commercial.
    from app.platform.services.dealership_settings import new_settings

    db.add(new_settings(dealership.id, actor_id))
    # KAN-204 (FR-A-03): a Betrieb always has an active location — the first
    # one is its registered seat, made with it.
    from app.platform.services.location import new_first_location

    new_first_location(db, dealership=dealership, actor_id=actor_id)
    # KAN-207 (D-A-17): its number ranges, with the neutral seeds, unfrozen.
    from app.platform.services.number_range import seed_number_ranges

    seed_number_ranges(db, dealership_id=dealership.id, actor_id=actor_id)

    record_audit_event(
        db,
        entity_type="dealership",
        entity_id=dealership.id,
        tenant_id=dealership.id,
        action="create",
        actor_id=actor_id,
        after={
            "legal_name": dealership.legal_name,
            "uid": dealership.uid,
            "vat_number": dealership.vat_number,
            "vat_liable": dealership.vat_liable,
            "status": _plain(dealership.status),
        },
    )
    db.commit()
    db.refresh(dealership)
    return dealership


def update_dealership(
    db: Session, *, dealership: Dealership, data: DealershipUpdate, actor_id: uuid.UUID
) -> Dealership:
    changes = data.model_dump(exclude_unset=True, exclude=set(_ADDRESS_BLOCKS) | _NOT_STORED)

    # KAN-203, the matrix row "Dealership legal identity": a change of legal
    # name, UID or VAT is saved only against an explicit confirmation (Anto,
    # 2026-10-24). A field sent with its current value is not a change.
    identity_changes = {
        field: value
        for field, value in changes.items()
        if field in LEGAL_IDENTITY_FIELDS and getattr(dealership, field) != value
    }
    if identity_changes and data.confirmation is not True:
        raise UnprocessableEntityError(
            "Changing the legal name, UID or VAT details needs your confirmation — send confirmation: true.",
            details={"reason": "confirmation_required", "fields": sorted(identity_changes)},
        )

    if "legal_form" in changes and changes["legal_form"] is not None:
        _assert_reference_codes(db, list_code=LEGAL_FORM_LIST_CODE, label="legalForm", codes=[changes["legal_form"]])
    if changes.get("association_memberships"):
        _assert_reference_codes(
            db,
            list_code=ASSOCIATION_MEMBERSHIP_LIST_CODE,
            label="associationMemberships",
            codes=list(changes["association_memberships"]),
        )
    if data.collective_vehicle_licences is not None:
        # Stored as JSON: every key present, the date as ISO text.
        changes["collective_vehicle_licences"] = [
            {
                "plate_number": licence.plate_number,
                "canton": licence.canton,
                "expires_on": licence.expires_on.isoformat() if licence.expires_on else None,
            }
            for licence in data.collective_vehicle_licences
        ]

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if dealership.status in _TERMINAL_DEALERSHIP_STATUSES and new_status != dealership.status:
            raise ConflictError(
                f"Dealership status '{dealership.status.value}' is terminal and cannot be changed.",
                details={"currentStatus": dealership.status.value},
            )

    _assert_vat_number_matches_uid(
        changes.get("uid", dealership.uid),
        changes.get("vat_number", dealership.vat_number),
    )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, value in changes.items():
        current = getattr(dealership, field)
        if current == value:
            continue
        before[field] = _plain(current)
        after[field] = _plain(value)
        setattr(dealership, field, value)

    if data.address is not None and data.address.model_dump() != dealership.address:
        before["address"], after["address"] = dealership.address, data.address.model_dump()
        _apply_address(dealership, "address", data.address)
    # An override sent as null clears the block (KAN-312 lists both as
    # nullable); one not sent at all is left alone.
    for block in ("correspondence_address", "billing_address"):
        if block in data.model_fields_set:
            new_block = getattr(data, block)
            new_value = new_block.model_dump() if new_block is not None else None
            if new_value != getattr(dealership, block):
                before[block], after[block] = getattr(dealership, block), new_value
            _apply_address(dealership, block, new_block)

    dealership.updated_by = actor_id
    dealership.version += 1

    # Flush the row itself before the audit row: a duplicate UID surfaces
    # here (uq_dealership_uid) as a 409, not as the audit helper's flush.
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _uid_conflict(changes.get("uid")) from exc

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
            reason=data.audit_note if identity_changes else None,
        )

    db.commit()
    db.refresh(dealership)
    return dealership
