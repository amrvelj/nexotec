"""The contact list and the duplicate-check typeahead (split from contact.py,
KAN-617): search predicates, the list's filters and its contact-map batch.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    AddressType,
    Contact,
    ContactAddress,
    ContactEmail,
    ContactGroupAssignment,
    ContactLifecycleStatus,
    ContactPhone,
    ContactRole,
    ContactType,
    Language,
)
from app.contact.schemas.contact import (
    CalendarPeriod,
)
from app.contact.services.contact_common import _DUPLICATE_CHECK_LIMIT, _MIN_PHONE_SEARCH_DIGITS, logger
from app.contact.services.roles import CUSTOMER_LIFECYCLE_STATUSES, holds_role
from app.core.blind_index import uid_search_key
from app.core.config import get_settings
from app.core.pagination import SortPageParams, count_capped, paginate_query_sorted
from app.core.relative_dates import start_of_current_period, start_of_day_days_ago
from app.core.sorting import build_page_for_sort
from app.core.text_collation import sort_by_text
from app.core.validators import normalise_phone


def _search_predicate(group_id: uuid.UUID, q: str):
    """The FR-01 "one search box" predicate, shared by list and duplicate
    check. Group-wide since WP-3 PR-2 (ADR-014) — search operates across
    every dealership in the group, not just one.

    Covers company_name and contact_number, which the pre-Phase-B version
    did not: a business contact could not be found by its own name, and no
    search reached the number staff actually quote (D-06). Phone matching
    goes through the normalised column so '079 123 45 67' finds a number
    stored as '+41791234567'. A whole UID finds its business contact through
    the blind index (KAN-819).
    """

    pattern = f"%{q}%"
    conditions = [
        Contact.first_name.ilike(pattern),
        Contact.last_name.ilike(pattern),
        Contact.company_name.ilike(pattern),
        Contact.contact_number.ilike(pattern),
        Contact.id.in_(
            select(ContactEmail.contact_id).where(
                ContactEmail.group_id == group_id,
                ContactEmail.email_address.ilike(pattern),
            )
        ),
    ]
    # KAN-819: a whole UID in any common spelling (CHE-123.456.789,
    # CHE123456789, with or without MWST/TVA/IVA) finds the business contact
    # holding it — one indexed equality on the keyed blind index, since
    # `tax_id` itself is encrypted. A part of a UID finds nothing by it.
    uid_key = uid_search_key(q)
    if uid_key is not None:
        conditions.append(Contact.tax_id_search_key == uid_key)
    phone_digits = normalise_phone(q)
    if len(phone_digits) >= _MIN_PHONE_SEARCH_DIGITS:
        conditions.append(
            Contact.id.in_(
                select(ContactPhone.contact_id).where(
                    ContactPhone.group_id == group_id,
                    ContactPhone.phone_normalised.like(f"%{phone_digits}%"),
                )
            )
        )
    return or_(*conditions)


def _start_of_day_days_ago(days: int, now: dt.datetime | None = None) -> dt.datetime:
    """KAN-474's boundary, now `app.core.relative_dates` (KAN-461)."""
    return start_of_day_days_ago(days, now)


def _start_of_current_period(period: CalendarPeriod, now: dt.datetime | None = None) -> dt.datetime:
    """The grid's «heute», «diese Woche», «dieser Monat», «dieses Jahr» (U-09,
    KAN-474), resolved by `app.core.relative_dates` (KAN-461)."""
    return start_of_current_period(period.value, now)


def _carries_contact_group(group_id: uuid.UUID, value_code: str):
    return (
        select(ContactGroupAssignment.id)
        .where(
            ContactGroupAssignment.contact_id == Contact.id,
            ContactGroupAssignment.group_id == group_id,
            ContactGroupAssignment.value_code == value_code,
        )
        .exists()
    )


def list_contacts(
    db: Session,
    *,
    group_id: uuid.UUID,
    q: str | None,
    lifecycle_status: ContactLifecycleStatus | None,
    contact_type: ContactType | None = None,
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
    lifecycle_status_not: ContactLifecycleStatus | None = None,
    contact_type_not: ContactType | None = None,
    language_not: Language | None = None,
    canton_not: str | None = None,
    advisor_user_id_not: uuid.UUID | None = None,
    assigned: bool = False,
    contact_group: str | None = None,
    contact_group_not: str | None = None,
    role: ContactRole | None = None,
    role_not: ContactRole | None = None,
) -> tuple[list[Contact], str | None, int, bool]:
    """`advisor_user_id` narrows to one advisor's contacts; `unassigned` to
    those with none — the queue FR-A-37 names, which a leaver's hand-over
    to nobody fills (KAN-200). Both together is an empty page.

    `updated_more_than_days_ago=N` keeps contacts last changed before the
    start of the calendar day N days ago in the session time zone — the
    grid's relative «more than N days ago», resolved here against the
    server's clock (U-09, KAN-458). `updated_in_last_days=N` keeps the
    complement — changed since that same midnight — so the two partition the
    list; `updated_since_start_of` keeps those changed since the current
    day, week, month or year began there (KAN-474). `updated_since` stays the
    absolute lower bound for incremental sync.

    The `…_not` parameters are the grid's «ist nicht» (KAN-476): a contact
    with no value for the field (no language, no primary domicile canton,
    no advisor) is kept, since it is not that value; `assigned` keeps those
    with an advisor. A merged contact stays hidden as without them.

    `contact_group` keeps the contacts carrying that group (KAN-466);
    `contact_group_not` those that do not — a contact with no group at all
    included.

    `role` keeps the contacts holding that role today, `role_not` those
    that do not (KAN-464). Prospect / active / inactive and the advisor are
    the customer role's (Q1, `services/roles.py`): those lifecycle filters
    and the unassigned queue match only contacts holding it, and their «ist
    nicht» keeps a contact without it, which has no such value (ADR-102)."""

    stmt = select(Contact).where(Contact.group_id == group_id)
    if advisor_user_id is not None:
        stmt = stmt.where(Contact.advisor_id == advisor_user_id)
    if unassigned:
        # FR-A-37's queue is the contacts nobody advises; a supplier has no advisor to give.
        stmt = stmt.where(Contact.advisor_id.is_(None), holds_role(ContactRole.CUSTOMER))
    if q:
        stmt = stmt.where(_search_predicate(group_id, q))
    if lifecycle_status is not None:
        stmt = stmt.where(Contact.lifecycle_status == lifecycle_status)
        if lifecycle_status in CUSTOMER_LIFECYCLE_STATUSES:
            stmt = stmt.where(holds_role(ContactRole.CUSTOMER))
    elif not include_merged:
        # A merged record is a tombstone pointing at its survivor. Showing it
        # in normal search results is how staff end up re-opening the record
        # they just merged away, so it is hidden unless explicitly asked for
        # (or explicitly filtered to, via lifecycle_status=merged).
        stmt = stmt.where(Contact.lifecycle_status != ContactLifecycleStatus.MERGED)
    if contact_type is not None:
        stmt = stmt.where(Contact.contact_type == contact_type)
    if language is not None:
        stmt = stmt.where(Contact.language == language)
    if canton is not None:
        # ADR-067 (WP-3 PR-5): canton is a fact of the primary domicile
        # ContactAddress row now, not the (frozen, read-only-mirror)
        # Contact.address_canton flat column. NOTE: this predicate is a
        # raw is_primary match — it deliberately does NOT apply the
        # `_is_usable_row` filter or the oldest-usable fallback that the
        # `address` projection (and `has_usable_domicile_address`) use, so
        # it can diverge for legacy / bulk-imported rows. Aligning it needs
        # the fallback replicated SQL-side — tracked as its own ticket.
        stmt = stmt.where(
            Contact.id.in_(
                select(ContactAddress.contact_id).where(
                    ContactAddress.group_id == group_id,
                    ContactAddress.address_type == AddressType.DOMICILE,
                    ContactAddress.is_primary.is_(True),
                    ContactAddress.address_canton == canton,
                )
            )
        )
    if lifecycle_status_not is not None:
        if lifecycle_status_not in CUSTOMER_LIFECYCLE_STATUSES:
            stmt = stmt.where(
                or_(Contact.lifecycle_status != lifecycle_status_not, ~holds_role(ContactRole.CUSTOMER))
            )
        else:
            stmt = stmt.where(Contact.lifecycle_status != lifecycle_status_not)
    if role is not None:
        stmt = stmt.where(holds_role(role))
    if role_not is not None:
        stmt = stmt.where(~holds_role(role_not))
    if contact_type_not is not None:
        stmt = stmt.where(Contact.contact_type != contact_type_not)
    if language_not is not None:
        stmt = stmt.where(Contact.language.is_distinct_from(language_not))
    if canton_not is not None:
        # The same raw is_primary domicile match as `canton` above, negated.
        stmt = stmt.where(
            Contact.id.not_in(
                select(ContactAddress.contact_id).where(
                    ContactAddress.group_id == group_id,
                    ContactAddress.address_type == AddressType.DOMICILE,
                    ContactAddress.is_primary.is_(True),
                    ContactAddress.address_canton == canton_not,
                )
            )
        )
    if advisor_user_id_not is not None:
        stmt = stmt.where(Contact.advisor_id.is_distinct_from(advisor_user_id_not))
    if assigned:
        stmt = stmt.where(Contact.advisor_id.is_not(None))
    if contact_group is not None:
        stmt = stmt.where(_carries_contact_group(group_id, contact_group))
    if contact_group_not is not None:
        stmt = stmt.where(~_carries_contact_group(group_id, contact_group_not))
    if updated_since is not None:
        stmt = stmt.where(Contact.updated_at >= updated_since)
    if updated_more_than_days_ago is not None:
        stmt = stmt.where(Contact.updated_at < _start_of_day_days_ago(updated_more_than_days_ago))
    if updated_in_last_days is not None:
        stmt = stmt.where(Contact.updated_at >= _start_of_day_days_ago(updated_in_last_days))
    if updated_since_start_of is not None:
        stmt = stmt.where(Contact.updated_at >= _start_of_current_period(updated_since_start_of))
    # Counted before pagination is applied (no ORDER BY/LIMIT/cursor yet) —
    # see count_capped for why this can never turn into a full table scan.
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=Contact, params=params)
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
        # decode) and re-hydrating each contact individually, skipping —
        # and logging — whichever single row still doesn't decode. One
        # corrupt contact must never be able to take the whole list down
        # for every other contact at the same dealer.
        db.rollback()
        id_stmt = stmt.with_only_columns(Contact.id)
        ids = list(db.scalars(id_stmt).all())
        rows = []
        for contact_id in ids:
            try:
                contact = db.get(Contact, contact_id)
            except LookupError:
                db.rollback()
                logger.error(
                    "contact %s has a column value that no longer decodes against its enum — "
                    "omitted from this list page rather than failing the whole request",
                    contact_id,
                )
                continue
            if contact is not None:
                rows.append(contact)
    items, next_cursor = build_page_for_sort(rows, params)
    return items, next_cursor, total, total_is_estimate


def _primary_contact_maps(db: Session, contact_ids):
    """Primary phone/email per contact, in two queries rather than 2N."""

    phones: dict[uuid.UUID, str] = {}
    emails: dict[uuid.UUID, str] = {}
    if not contact_ids:
        return phones, emails
    for phone_row in db.scalars(select(ContactPhone).where(ContactPhone.contact_id.in_(contact_ids))).all():
        if phone_row.is_primary or phone_row.contact_id not in phones:
            phones[phone_row.contact_id] = phone_row.phone_e164
    for email_row in db.scalars(select(ContactEmail).where(ContactEmail.contact_id.in_(contact_ids))).all():
        if email_row.is_primary or email_row.contact_id not in emails:
            emails[email_row.contact_id] = email_row.email_address
    return phones, emails


def duplicate_check(db: Session, *, group_id: uuid.UUID, q: str) -> list[dict[str, Any]]:
    """Advisory typeahead (Swiss addendum Round 2 #5) — never a blocking gate
    on create. A dealership sometimes has a legitimate reason to create a
    near-identical record; FR-09 merge exists for the rest. Group-wide since
    WP-3 PR-2 (ADR-014, PRD-Contacts FR-04).

    Returns plain dicts rather than Contact rows because a candidate is not
    a contact: it carries a match reason and the primary contact details,
    and it must render for a *business* contact too. The old version typed
    first_name/last_name as required, so a company among the candidates blew
    up serialisation — duplicate detection failed exactly when it found a
    company (D-07).
    """

    candidates = list(
        db.scalars(
            select(Contact)
            .where(
                Contact.group_id == group_id,
                Contact.lifecycle_status != ContactLifecycleStatus.MERGED,
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
    needle_uid_key = uid_search_key(q)

    exact_email_ids = {
        row.contact_id
        for row in db.scalars(
            select(ContactEmail).where(
                ContactEmail.group_id == group_id,
                func.lower(ContactEmail.email_address) == needle,
            )
        ).all()
    }
    exact_phone_ids = set()
    if len(needle_digits) >= 7:
        exact_phone_ids = {
            row.contact_id
            for row in db.scalars(
                select(ContactPhone).where(
                    ContactPhone.group_id == group_id,
                    ContactPhone.phone_normalised == needle_digits,
                )
            ).all()
        }

    results = []
    for contact in candidates:
        is_exact = (
            contact.id in exact_email_ids
            or contact.id in exact_phone_ids
            # KAN-819: the same UID is the same company.
            or (needle_uid_key is not None and contact.tax_id_search_key == needle_uid_key)
        )
        results.append(
            {
                "id": contact.id,
                "contact_number": contact.contact_number,
                "contact_type": contact.contact_type,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "company_name": contact.company_name,
                "primary_phone": phones.get(contact.id),
                "primary_email": emails.get(contact.id),
                "lifecycle_status": contact.lifecycle_status,
                "match": "exact" if is_exact else "similar",
            }
        )

    # Exact matches first, each group by name under the collation (KAN-769): `sort` is stable.
    results = sort_by_text(db, results, key=lambda r: r["last_name"] or r["company_name"] or "")
    results.sort(key=lambda r: r["match"] != "exact")
    return results[:_DUPLICATE_CHECK_LIMIT]
