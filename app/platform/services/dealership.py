"""Dealership service layer: create/read/update + the audit-logging and
lifecycle rules the spec calls out (legal identity — legal name, UID, VAT
number, VAT liability — and status changes are audit-logged, the permission
matrix's "Dealership legal identity" row; `offboarded` is a terminal status).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import BadRequestError, ConflictError, NotFoundError, UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus, Location
from app.platform.models.user import User
from app.platform.schemas.dealership import (
    DealershipAddress,
    DealershipCreate,
    DealershipOverrideAddress,
    DealershipUpdate,
)

# The UID is public (D-A-22), so none of these is redacted.
_AUDITED_FIELDS = {"legal_name", "uid", "vat_number", "vat_liable", "status"}
_TERMINAL_DEALERSHIP_STATUSES = {DealershipStatus.OFFBOARDED}
_ADDRESS_FIELDS = ("street", "house_number", "postal_code", "locality", "canton", "country")
_ADDRESS_BLOCKS = ("address", "correspondence_address", "billing_address")


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _apply_address(
    dealership: Dealership, prefix: str, address: DealershipAddress | DealershipOverrideAddress | None
) -> None:
    """Write one address block's six columns, or clear all six (D-A-25: an
    override is whole or absent). The registered seat (`address`) is never
    cleared — its schema field is not nullable."""

    for field in _ADDRESS_FIELDS:
        setattr(dealership, f"{prefix}_{field}", getattr(address, field) if address is not None else None)


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
    changes = data.model_dump(exclude_unset=True, exclude=set(_ADDRESS_BLOCKS))

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
        if field in _AUDITED_FIELDS:
            before[field] = _plain(current)
            after[field] = _plain(value)
        setattr(dealership, field, value)

    if data.address is not None:
        _apply_address(dealership, "address", data.address)
    # An override sent as null clears the block (KAN-312 lists both as
    # nullable); one not sent at all is left alone.
    for block in ("correspondence_address", "billing_address"):
        if block in data.model_fields_set:
            _apply_address(dealership, block, getattr(data, block))

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
        )

    db.commit()
    db.refresh(dealership)
    return dealership


def get_group_display_labels(
    db: Session, *, group_id: uuid.UUID, dealership_ids: set[uuid.UUID], user_ids: set[uuid.UUID]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """KAN-371 (Anto, 2026-10-18, option A): display labels for ids another
    context recorded — a Betrieb's name (trade name, else legal name) and a
    user's `First Last` — resolved on read, for the Betriebe of `group_id`
    only. An id outside the group, or one that names nothing, is simply
    absent from the result: the caller renders it as unknown, so nothing here
    confirms a record of another group exists."""

    dealerships: dict[uuid.UUID, str] = {}
    if dealership_ids:
        for dealership_id, trade_name, legal_name in db.execute(
            select(Dealership.id, Dealership.trade_name, Dealership.legal_name).where(
                Dealership.id.in_(dealership_ids), Dealership.dealer_group_id == group_id
            )
        ):
            dealerships[dealership_id] = trade_name or legal_name
    users: dict[uuid.UUID, str] = {}
    if user_ids:
        for user_id, first_name, last_name in db.execute(
            select(User.id, User.first_name, User.last_name)
            .join(Dealership, Dealership.id == User.tenant_id)
            .where(User.id.in_(user_ids), Dealership.dealer_group_id == group_id)
        ):
            users[user_id] = f"{first_name} {last_name}".strip()
    return dealerships, users
