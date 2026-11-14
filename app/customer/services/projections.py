"""The six contact read-model projections (ADR-067), computed on read and never
stored (split from customer.py, KAN-617).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models.customer import (
    AddressType,
    CustomerAddress,
    CustomerEmail,
    CustomerPhone,
    CustomerTag,
    EmailType,
    PhoneType,
)
from app.customer.schemas.customer import (
    CustomerAddressRead,
)
from app.customer.services.contact_groups import contact_groups_by_customer
from app.customer.services.customer_common import _is_usable_row

_EMPTY_PROJECTIONS: dict[str, Any] = {
    "phone_mobile": None,
    "phone_landline": None,
    "phone_work": None,
    "email": None,
    "email_secondary": None,
    "address": None,
    "tags": [],
    "contact_groups": [],
}


def compute_customer_projections(db: Session, customer_id: uuid.UUID) -> dict[str, Any]:
    """Single-customer form of compute_customer_projections_batch — for the
    get/create/update/merge endpoints, which only ever need one customer's
    projections at a time.
    """

    return compute_customer_projections_batch(db, [customer_id]).get(customer_id, dict(_EMPTY_PROJECTIONS))


def compute_customer_projections_batch(db: Session, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
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

    contact_groups = contact_groups_by_customer(db, customer_ids)

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
            "contact_groups": contact_groups.get(customer_id, []),
        }
    return result
