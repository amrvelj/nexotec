"""The customer list and the duplicate-check typeahead (split from customer.py,
KAN-617): search predicates, the list's filters and its contact-map batch.
"""

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.core.validators import normalise_phone
from app.customer.models.customer import (
    AddressType,
    Customer,
    CustomerAddress,
    CustomerEmail,
    CustomerLifecycleStatus,
    CustomerPhone,
    CustomerType,
    Language,
)
from app.customer.schemas.customer import (
    CalendarPeriod,
)
from app.customer.services.customer_common import _DUPLICATE_CHECK_LIMIT, _MIN_PHONE_SEARCH_DIGITS, logger


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


def _today_in_session_zone(now: dt.datetime | None) -> tuple[dt.date, ZoneInfo]:
    zone = ZoneInfo(get_settings().session_timezone)
    return (now or dt.datetime.now(dt.UTC)).astimezone(zone).date(), zone


def _start_of_day_days_ago(days: int, now: dt.datetime | None = None) -> dt.datetime:
    """Midnight, in the session time zone, of the calendar day `days` before
    today there. `now` pins the clock for a test."""
    today, zone = _today_in_session_zone(now)
    return dt.datetime.combine(today - dt.timedelta(days=days), dt.time.min, tzinfo=zone)


def _start_of_current_period(period: CalendarPeriod, now: dt.datetime | None = None) -> dt.datetime:
    """Midnight, in the session time zone, at which the current calendar day,
    week (Monday, ISO), month or year began there — the grid's «heute»,
    «diese Woche», «dieser Monat», «dieses Jahr» (U-09, KAN-474). `now` pins
    the clock for a test."""
    today, zone = _today_in_session_zone(now)
    if period is CalendarPeriod.DAY:
        start = today
    elif period is CalendarPeriod.WEEK:
        start = today - dt.timedelta(days=today.weekday())
    elif period is CalendarPeriod.MONTH:
        start = today.replace(day=1)
    else:
        start = today.replace(month=1, day=1)
    return dt.datetime.combine(start, dt.time.min, tzinfo=zone)


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
    updated_more_than_days_ago: int | None = None,
    updated_in_last_days: int | None = None,
    updated_since_start_of: CalendarPeriod | None = None,
    lifecycle_status_not: CustomerLifecycleStatus | None = None,
    customer_type_not: CustomerType | None = None,
    language_not: Language | None = None,
    canton_not: str | None = None,
    advisor_user_id_not: uuid.UUID | None = None,
    assigned: bool = False,
) -> tuple[list[Customer], str | None, int, bool]:
    """`advisor_user_id` narrows to one advisor's customers; `unassigned` to
    those with none — the queue FR-A-37 names, which a leaver's hand-over
    to nobody fills (KAN-200). Both together is an empty page.

    `updated_more_than_days_ago=N` keeps customers last changed before the
    start of the calendar day N days ago in the session time zone — the
    grid's relative «more than N days ago», resolved here against the
    server's clock (U-09, KAN-458). `updated_in_last_days=N` keeps the
    complement — changed since that same midnight — so the two partition the
    list; `updated_since_start_of` keeps those changed since the current
    day, week, month or year began there (KAN-474). `updated_since` stays the
    absolute lower bound for incremental sync.

    The `…_not` parameters are the grid's «ist nicht» (KAN-476): a customer
    with no value for the field (no language, no primary domicile canton,
    no advisor) is kept, since it is not that value; `assigned` keeps those
    with an advisor. A merged customer stays hidden as without them."""

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
    if lifecycle_status_not is not None:
        stmt = stmt.where(Customer.lifecycle_status != lifecycle_status_not)
    if customer_type_not is not None:
        stmt = stmt.where(Customer.customer_type != customer_type_not)
    if language_not is not None:
        stmt = stmt.where(Customer.language.is_distinct_from(language_not))
    if canton_not is not None:
        # The same raw is_primary domicile match as `canton` above, negated.
        stmt = stmt.where(
            Customer.id.not_in(
                select(CustomerAddress.customer_id).where(
                    CustomerAddress.group_id == group_id,
                    CustomerAddress.address_type == AddressType.DOMICILE,
                    CustomerAddress.is_primary.is_(True),
                    CustomerAddress.address_canton == canton_not,
                )
            )
        )
    if advisor_user_id_not is not None:
        stmt = stmt.where(Customer.advisor_id.is_distinct_from(advisor_user_id_not))
    if assigned:
        stmt = stmt.where(Customer.advisor_id.is_not(None))
    if updated_since is not None:
        stmt = stmt.where(Customer.updated_at >= updated_since)
    if updated_more_than_days_ago is not None:
        stmt = stmt.where(Customer.updated_at < _start_of_day_days_ago(updated_more_than_days_ago))
    if updated_in_last_days is not None:
        stmt = stmt.where(Customer.updated_at >= _start_of_day_days_ago(updated_in_last_days))
    if updated_since_start_of is not None:
        stmt = stmt.where(Customer.updated_at >= _start_of_current_period(updated_since_start_of))
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
