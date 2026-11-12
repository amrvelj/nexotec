"""Helpers every customer service module shares (split from customer.py, KAN-617):
the outbox producer name, the group-scoped 404 lookups, a customer's display
name and label payload, the contact-row usability test and the single-primary
fix-up, country-code validation.
"""

import datetime as dt
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.customer.models.customer import (
    Customer,
    CustomerAddress,
    CustomerEmail,
    CustomerPhone,
)
from app.platform.public import (
    get_active_reference_value_codes,
)

logger = logging.getLogger("app.customer")
# tags live in a child table (no `customer.tags` attribute) — audited
# explicitly by create_customer / update_customer, not via the generic
# _AUDITED_FIELDS comprehension.
# Below this many digits a phone fragment matches most of the table, so it is
# treated as a name search instead.
_MIN_PHONE_SEARCH_DIGITS = 3
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


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint a Postgres IntegrityError violated (None on
    SQLite, whose driver does not report it)."""

    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


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


def _is_usable_row(row: Any) -> bool:
    """A contact-channel row is usable when it is neither closed (valid_to
    set) nor flagged do_not_use — the same definition FR-03 and the six
    projections use.
    """

    return row.valid_to is None and not row.do_not_use


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


def customer_display_name(customer: Customer) -> str:
    """Company name, else first+last, else customer number — the same
    precedence `resolve_customer_label` uses in `sales`, which keeps its own
    copy rather than importing this. Exported through customer.public for
    the vehicle context's party-roles and allocate responses (KAN-140)."""

    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number
