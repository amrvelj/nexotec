"""Contact endpoints (issue #4).

Unlike Dealership/User, these routes are flat (`/v1/contacts`, not
`/v1/dealerships/{id}/contacts`) — the spec's literal endpoint list has no
dealership_id path segment. Group-scoped since WP-3 PR-2 (ADR-014): scope is
resolved purely from the JWT (principal.group_id), with no path param to
validate against. This also means platform_admin has no special cross-group
reach here (unlike Dealership, where require_tenant_match explicitly lets
platform_admin bypass the match) — Round 3's access-control notes scope
platform_admin's cross-tenant power to "dealer onboarding only," and
Contact isn't part of that.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.audit_schemas import AuditEventRead
from app.core.auth import Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import SortPageParams
from app.core.permissions import may_write, require_read, require_write
from app.core.sorting import SortField, decode_cursor_for_sort, parse_sort
from app.core.validators import CantonCode
from app.contact.models.contact import ContactRole, Contact, ContactLifecycleStatus, ContactType, Language
from app.contact.schemas.contact import (
    CalendarPeriod,
    ContactAddressCreate,
    ContactAddressPage,
    ContactAddressRead,
    ContactAddressUpdate,
    ContactAdvisorOption,
    ContactAdvisorOptionList,
    ContactCreate,
    ContactCreditBlockRequest,
    ContactDuplicateCandidate,
    ContactDuplicateCandidateList,
    ContactEmailCreate,
    ContactEmailPage,
    ContactEmailRead,
    ContactEmailUpdate,
    ContactExternalIdCreate,
    ContactExternalIdPage,
    ContactExternalIdRead,
    ContactExternalIdUpdate,
    ContactHistoryPage,
    ContactMergeRequest,
    ContactPage,
    ContactPhoneCreate,
    ContactPhonePage,
    ContactPhoneRead,
    ContactPhoneUpdate,
    ContactRead,
    ContactUpdate,
    ContactVehicleCreate,
    ContactVehicleRead,
    ContactVehicleUpdate,
)
from app.contact.schemas.legal_basis import LegalBasisCreate, LegalBasisRead
from app.contact.services import addresses as addresses_service
from app.contact.services import contact_channels as contact_channels_service
from app.contact.services import contact as contact_service
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from app.contact.services import contact_search as contact_search_service
from app.contact.services import external_ids as external_ids_service
from app.contact.services import legal_basis as legal_basis_service
from app.contact.services import projections as projections_service
from app.contact.services import roles as roles_service
from app.contact.services import vehicle_party as vehicle_party_service
from app.contact.services import vehicle_party_reads as vehicle_party_reads_service
from app.db import get_db
from app.platform.public import get_dealership_or_404

router = APIRouter(tags=["contacts"], route_class=IdempotentRoute)
settings = get_settings()

# U-02/U-03: only columns with a supporting index are offered as sortable —
# see alembic/versions/d2f7b0e9c453_customer_sort_indexes.py for last_name/
# updated_at/created_at; contact_number and company_name were already
# indexed (D-06). Keys are the API-facing (camelCase) field name, exactly
# as the UI/UX doc's own saved-view example uses them
# (`{"field": "lastName", "direction": "asc"}`).
CONTACT_SORT_FIELDS: dict[str, object] = {
    "contactNumber": Contact.contact_number,
    "companyName": Contact.company_name,
    "lastName": Contact.last_name,
    "updatedAt": Contact.updated_at,
    "createdAt": Contact.created_at,
}
# "Default sort: Declared per grid... For most grids: updatedAt:desc"
# (UI/UX Core Principles FR-UI-01) — used when the client sends no `sort`.
_DEFAULT_CONTACT_SORT = [
    SortField(api_name="updatedAt", column=Contact.updated_at, direction="desc", nullable=False)
]


def _may_write_customer_role(principal: Principal) -> bool:
    """KAN-464 Q3: the matrix cell of `contact_customer_role` — who may
    give the customer role and write its data (services/roles.py)."""

    return may_write(
        roles_service.CUSTOMER_ROLE_CAPABILITY, roles=principal.roles, is_dealer_manager=principal.is_dealer_manager
    )


def _contact_read(db: Session, contact: Contact) -> ContactRead:
    """ContactRead plus the ADR-067 contact projections and the KAN-50
    `tags` list, all computed here (never stored) and layered on with
    model_copy — the base model_validate leaves them at their schema
    default.
    """

    base = ContactRead.model_validate(contact, from_attributes=True)
    return base.model_copy(update=projections_service.compute_contact_projections(db, contact.id))


@router.post("/contacts", response_model=ContactRead, status_code=201)
def create_contact(
    body: ContactCreate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    # The ACTIVE dealership is JWT-derived here (no path param to validate —
    # see module docstring), so unlike Dealership/User this 404 guards
    # against a token whose tenant_id claim doesn't match a real Dealership,
    # catching that as a clean 404 instead of an unhandled FK-violation 500
    # on insert. Contact itself is created group-scoped (principal.group_id)
    # — the dealership check is just proving the caller's session is real.
    get_dealership_or_404(db, principal.tenant_id)

    contact = contact_service.create_contact(
        db,
        group_id=principal.group_id,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        may_write_customer_role=_may_write_customer_role(principal),
        dealership_id=principal.tenant_id,
    )
    return _contact_read(db, contact)


@router.get("/contacts/duplicate-check", response_model=ContactDuplicateCandidateList)
def duplicate_check(
    q: str = Query(min_length=1),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    rows = contact_search_service.duplicate_check(db, group_id=principal.group_id, q=q)
    # Plain dicts, not ORM rows — see services.contact.duplicate_check for
    # why a candidate is not simply a Contact.
    return ContactDuplicateCandidateList(
        items=[ContactDuplicateCandidate.model_validate(row) for row in rows]
    )


@router.get("/contacts/advisor-options", response_model=ContactAdvisorOptionList)
def list_contact_advisor_options(
    principal: Principal = Depends(require_read("contacts")),
    db: Session = Depends(get_db),
):
    """The advisor picker's options (KAN-50 / D-24) — everyone who may
    advise in the ACTING dealership: its active and invited users and its
    active members (KAN-410). Declared before `/contacts/{contact_id}` so the
    literal path is matched first. Gated on the `contacts` read
    capability, not the manager-only `dealership_users` one: a plain
    advisor must be able to (re)assign a contact's advisor.
    """

    rows = contact_service.list_advisor_options(db, dealership_id=principal.tenant_id)
    return ContactAdvisorOptionList(items=[ContactAdvisorOption.model_validate(row) for row in rows])


@router.get("/contacts/{contact_id}", response_model=ContactRead)
def get_contact(
    contact_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    return _contact_read(db, contact)


@router.patch("/contacts/{contact_id}", response_model=ContactRead)
def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    check_version(contact.version, if_match, entity_name="Contact")
    contact = contact_service.update_contact(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        may_write_customer_role=_may_write_customer_role(principal),
        dealership_id=principal.tenant_id,
    )
    return _contact_read(db, contact)


@router.post("/contacts/{contact_id}/credit-block", response_model=ContactRead)
def set_credit_block(
    contact_id: uuid.UUID,
    body: ContactCreditBlockRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    """WP-8 PR-6 (ADR-065/S-D19) — stops a contract, never an offer."""

    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    check_version(contact.version, if_match, entity_name="Contact")
    contact = contact_service.set_credit_block(
        db,
        contact=contact,
        blocked=body.blocked,
        reason=body.reason,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        may_write_customer_role=_may_write_customer_role(principal),
    )
    return _contact_read(db, contact)


@router.post("/contacts/{contact_id}/merge", response_model=ContactRead)
def merge_contact(
    contact_id: uuid.UUID,
    body: ContactMergeRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    check_version(contact.version, if_match, entity_name="Contact")
    contact = contact_merge_service.merge_contact(
        db,
        contact=contact,
        duplicate_of_contact_id=body.duplicate_of_contact_id,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        may_write_customer_role=_may_write_customer_role(principal),
    )
    return _contact_read(db, contact)


@router.get("/contacts", response_model=ContactPage)
def list_contacts(
    q: str | None = None,
    lifecycle_status: ContactLifecycleStatus | None = None,
    contact_type: ContactType | None = None,
    language: Language | None = None,
    canton: str | None = None,
    updated_since: dt.datetime | None = None,
    updated_more_than_days_ago: int | None = Query(
        default=None,
        ge=0,
        le=36500,
        description=(
            "KAN-458: contacts last changed before the start of the day N days ago (Europe/Zurich) — the grid's "
            "relative «more than N days ago» filter, resolved against the server's clock, never the client's."
        ),
    ),
    updated_in_last_days: int | None = Query(
        default=None,
        ge=0,
        le=36500,
        description=(
            "KAN-474: contacts changed since the start of the day N days ago (Europe/Zurich) — the grid's "
            "«in the last N days»; with updated_more_than_days_ago=N it partitions the list."
        ),
    ),
    updated_since_start_of: CalendarPeriod | None = Query(
        default=None,
        description=(
            "KAN-474: contacts changed since the current day, week (Monday), month or year began in "
            "Europe/Zurich — the grid's «today», «this week», «this month», «this year», resolved server-side."
        ),
    ),
    include_merged: bool = False,
    advisor_user_id: uuid.UUID | None = Query(default=None, alias="advisorUserId"),
    unassigned: bool = Query(
        default=False, description="KAN-200: contacts with no advisor — the queue a leaver's hand-over fills."
    ),
    # KAN-476: «ist nicht» per select field. A contact without a value (no
    # language, no domicile canton, no advisor) is kept: it is not that value.
    lifecycle_status_not: ContactLifecycleStatus | None = Query(
        default=None, description="KAN-476: contacts whose lifecycle status is not this one."
    ),
    contact_type_not: ContactType | None = Query(default=None, description="KAN-476: contacts not of this type."),
    language_not: Language | None = Query(
        default=None, description="KAN-476: contacts whose language is not this one (none set included)."
    ),
    canton_not: CantonCode | None = Query(
        default=None,
        description="KAN-476: contacts whose primary domicile is not in this canton (none recorded included).",
    ),
    advisor_user_id_not: uuid.UUID | None = Query(
        default=None,
        alias="advisorUserIdNot",
        description="KAN-476: contacts this user does not advise (unassigned ones included).",
    ),
    assigned: bool = Query(default=False, description="KAN-476: contacts that have an advisor — «ist nicht Nicht zugewiesen»."),
    contact_group: str | None = Query(
        default=None,
        alias="contactGroup",
        max_length=64,
        description="KAN-466: contacts carrying this contact group (a code of the platform list contact_group).",
    ),
    contact_group_not: str | None = Query(
        default=None,
        alias="contactGroupNot",
        max_length=64,
        description="KAN-466: contacts not carrying this contact group (contacts with none included).",
    ),
    role: ContactRole | None = Query(
        default=None, description="KAN-464: contacts holding this role today (`contact`, `supplier`)."
    ),
    role_not: ContactRole | None = Query(
        default=None, alias="roleNot", description="KAN-464: contacts not holding this role today."
    ),
    sort: str | None = Query(default=None, description="e.g. 'lastName:asc,updatedAt:desc'"),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    sort_fields = parse_sort(sort, allowed=CONTACT_SORT_FIELDS) or _DEFAULT_CONTACT_SORT
    params = SortPageParams(
        limit=limit, cursor=decode_cursor_for_sort(cursor, sort_fields), sort_fields=sort_fields
    )
    rows, next_cursor, total, total_is_estimate = contact_search_service.list_contacts(
        db,
        group_id=principal.group_id,
        q=q,
        lifecycle_status=lifecycle_status,
        contact_type=contact_type,
        language=language,
        canton=canton,
        updated_since=updated_since,
        updated_more_than_days_ago=updated_more_than_days_ago,
        updated_in_last_days=updated_in_last_days,
        updated_since_start_of=updated_since_start_of,
        params=params,
        include_merged=include_merged,
        advisor_user_id=advisor_user_id,
        unassigned=unassigned,
        lifecycle_status_not=lifecycle_status_not,
        contact_type_not=contact_type_not,
        language_not=language_not,
        canton_not=canton_not,
        advisor_user_id_not=advisor_user_id_not,
        assigned=assigned,
        contact_group=contact_group,
        contact_group_not=contact_group_not,
        role=role,
        role_not=role_not,
    )
    projections_by_id = projections_service.compute_contact_projections_batch(db, [c.id for c in rows])
    items = [
        ContactRead.model_validate(c, from_attributes=True).model_copy(update=projections_by_id.get(c.id, {}))
        for c in rows
    ]
    return ContactPage(
        items=items,
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/contacts/{contact_id}/audit-log", response_model=ContactHistoryPage)
def get_contact_audit_log(
    contact_id: uuid.UUID,
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    # KAN-722: without an offboarded sister Betrieb's entries.
    events = contact_service.list_contact_history(db, group_id=principal.group_id, contact_id=contact_id)
    # KAN-371: the ids the events carry are named on read, group-scoped.
    return ContactHistoryPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in events],
        next_cursor=None,
        labels=contact_service.history_labels(
            db, group_id=principal.group_id, dealership_id=principal.tenant_id, events=events
        ),
    )


# --- ContactPhone / ContactEmail: multi-valued contact details (Contact
# PRD, 2026-08-07). Same "contacts" write capability as the parent
# Contact (WP-2 PR-2); no If-Match
# on these child rows — no version column (CTO ruling: is_primary isn't a
# high-contention field), same reasoning as VehicleCustodyEvent being
# unversioned.


@router.get("/contacts/{contact_id}/phones", response_model=ContactPhonePage)
def list_contact_phones(
    contact_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    rows = contact_channels_service.list_contact_phones(db, contact_id=contact_id)
    return ContactPhonePage(items=[ContactPhoneRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/contacts/{contact_id}/phones", response_model=ContactPhoneRead, status_code=201)
def create_contact_phone(
    contact_id: uuid.UUID,
    body: ContactPhoneCreate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    phone = contact_channels_service.create_contact_phone(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactPhoneRead.model_validate(phone, from_attributes=True)


@router.patch("/contacts/{contact_id}/phones/{phone_id}", response_model=ContactPhoneRead)
def update_contact_phone(
    contact_id: uuid.UUID,
    phone_id: uuid.UUID,
    body: ContactPhoneUpdate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    phone = contact_channels_service.get_contact_phone_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, phone_id=phone_id
    )
    phone = contact_channels_service.update_contact_phone(
        db,
        phone=phone,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactPhoneRead.model_validate(phone, from_attributes=True)


@router.delete("/contacts/{contact_id}/phones/{phone_id}", status_code=204)
def delete_contact_phone(
    contact_id: uuid.UUID,
    phone_id: uuid.UUID,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    phone = contact_channels_service.get_contact_phone_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, phone_id=phone_id
    )
    contact_channels_service.delete_contact_phone(
        db,
        phone=phone,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )


@router.get("/contacts/{contact_id}/emails", response_model=ContactEmailPage)
def list_contact_emails(
    contact_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    rows = contact_channels_service.list_contact_emails(db, contact_id=contact_id)
    return ContactEmailPage(items=[ContactEmailRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/contacts/{contact_id}/emails", response_model=ContactEmailRead, status_code=201)
def create_contact_email(
    contact_id: uuid.UUID,
    body: ContactEmailCreate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    email = contact_channels_service.create_contact_email(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactEmailRead.model_validate(email, from_attributes=True)


@router.patch("/contacts/{contact_id}/emails/{email_id}", response_model=ContactEmailRead)
def update_contact_email(
    contact_id: uuid.UUID,
    email_id: uuid.UUID,
    body: ContactEmailUpdate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    email = contact_channels_service.get_contact_email_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, email_id=email_id
    )
    email = contact_channels_service.update_contact_email(
        db,
        email=email,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactEmailRead.model_validate(email, from_attributes=True)


@router.delete("/contacts/{contact_id}/emails/{email_id}", status_code=204)
def delete_contact_email(
    contact_id: uuid.UUID,
    email_id: uuid.UUID,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    email = contact_channels_service.get_contact_email_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, email_id=email_id
    )
    contact_channels_service.delete_contact_email(
        db,
        email=email,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )


# --- ContactAddress: multi-valued postal addresses (WP-3 PR-5, ADR-067).
# Same "contacts" write capability and unversioned-child-row reasoning as
# ContactPhone/ContactEmail above.


@router.get("/contacts/{contact_id}/addresses", response_model=ContactAddressPage)
def list_contact_addresses(
    contact_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    rows = addresses_service.list_contact_addresses(db, contact_id=contact_id)
    return ContactAddressPage(items=[ContactAddressRead.model_validate(r, from_attributes=True) for r in rows])


@router.post("/contacts/{contact_id}/addresses", response_model=ContactAddressRead, status_code=201)
def create_contact_address(
    contact_id: uuid.UUID,
    body: ContactAddressCreate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    address = addresses_service.create_contact_address(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactAddressRead.model_validate(address, from_attributes=True)


@router.patch("/contacts/{contact_id}/addresses/{address_id}", response_model=ContactAddressRead)
def update_contact_address(
    contact_id: uuid.UUID,
    address_id: uuid.UUID,
    body: ContactAddressUpdate,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    address = addresses_service.get_contact_address_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, address_id=address_id
    )
    address = addresses_service.update_contact_address(
        db,
        address=address,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactAddressRead.model_validate(address, from_attributes=True)


@router.delete("/contacts/{contact_id}/addresses/{address_id}", status_code=204)
def delete_contact_address(
    contact_id: uuid.UUID,
    address_id: uuid.UUID,
    principal: Principal = Depends(require_write("contacts")),
    db: Session = Depends(get_db),
):
    address = addresses_service.get_contact_address_or_404(
        db, group_id=principal.group_id, contact_id=contact_id, address_id=address_id
    )
    addresses_service.delete_contact_address(
        db,
        address=address,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )


# --- ContactExternalId: per-dealer CRM/OEM linkage. Write is
# platform_admin-only (Anto's ruling, 2026-08-07, overriding the earlier
# dealer_admin default) — read stays open to any authenticated tenant role,
# same pattern as an audit-log endpoint being readable but not writable by
# regular roles.


@router.get("/contacts/{contact_id}/external-ids", response_model=ContactExternalIdPage)
def list_contact_external_ids(
    contact_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    rows = external_ids_service.list_contact_external_ids(db, contact_id=contact_id)
    return ContactExternalIdPage(
        items=[ContactExternalIdRead.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post("/contacts/{contact_id}/external-ids", response_model=ContactExternalIdRead, status_code=201)
def create_contact_external_id(
    contact_id: uuid.UUID,
    body: ContactExternalIdCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    # Group-agnostic lookup, not get_contact_or_404(principal.group_id,
    # ...): platform_admin's principal.group_id is the dealer group of the
    # Betrieb the staff member signed in to (`_mint_session`), not the
    # contact's — and unlike Dealership's, Contact's own module docstring
    # explicitly scopes platform_admin's cross-tenant reach to "dealer
    # onboarding only." ContactExternalId is the one deliberate exception
    # to that (Anto's ruling, 2026-08-07: this is a platform-managed CRM/OEM
    # linkage, by design reachable across every dealer) — resolve the
    # contact's real group_id from the row itself instead.
    contact = contact_common_service.get_contact_by_id_or_404(db, contact_id)
    row = external_ids_service.create_contact_external_id(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactExternalIdRead.model_validate(row, from_attributes=True)


@router.patch("/contacts/{contact_id}/external-ids/{external_id_row_id}", response_model=ContactExternalIdRead)
def update_contact_external_id(
    contact_id: uuid.UUID,
    external_id_row_id: uuid.UUID,
    body: ContactExternalIdUpdate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_by_id_or_404(db, contact_id)
    row = external_ids_service.get_contact_external_id_or_404(
        db, group_id=contact.group_id, contact_id=contact_id, external_id_row_id=external_id_row_id
    )
    row = external_ids_service.update_contact_external_id(
        db,
        row=row,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactExternalIdRead.model_validate(row, from_attributes=True)


@router.delete("/contacts/{contact_id}/external-ids/{external_id_row_id}", status_code=204)
def delete_contact_external_id(
    contact_id: uuid.UUID,
    external_id_row_id: uuid.UUID,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_by_id_or_404(db, contact_id)
    row = external_ids_service.get_contact_external_id_or_404(
        db, group_id=contact.group_id, contact_id=contact_id, external_id_row_id=external_id_row_id
    )
    external_ids_service.delete_contact_external_id(
        db,
        row=row,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )


# --- Contact<->Vehicle relationships (D-12, FR-10): owner/keeper/driver,
# backing the 360 view's Vehicles tab. A vehicle-mdm record is global
# (ADR-022), so the tenant boundary is enforced entirely by resolving
# `contact` through get_contact_or_404 — the vehicle_id a caller supplies
# is looked up with no tenant filter. The list read (GET) is composed at the root,
# app/api/v1/contact_vehicles.py (KAN-632): it embeds Stock's answer.


@router.post("/contacts/{contact_id}/vehicles", response_model=ContactVehicleRead, status_code=201)
def create_contact_vehicle(
    contact_id: uuid.UUID,
    body: ContactVehicleCreate,
    principal: Principal = Depends(require_write("contact_vehicle_links")),
    db: Session = Depends(get_db),
):
    contact = contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    party = vehicle_party_service.create_contact_vehicle(
        db,
        contact=contact,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
    )
    return ContactVehicleRead.model_validate(party, from_attributes=True)


@router.patch("/contacts/{contact_id}/vehicles/{party_id}", response_model=ContactVehicleRead)
def update_contact_vehicle(
    contact_id: uuid.UUID,
    party_id: uuid.UUID,
    body: ContactVehicleUpdate,
    principal: Principal = Depends(require_write("contact_vehicle_links")),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    party = vehicle_party_reads_service.get_contact_vehicle_or_404(db, contact_id=contact_id, party_id=party_id)
    party = vehicle_party_service.update_contact_vehicle(
        db,
        party=party,
        data=body,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        group_id=principal.group_id,
    )
    return ContactVehicleRead.model_validate(party, from_attributes=True)


@router.delete("/contacts/{contact_id}/vehicles/{party_id}", status_code=204)
def delete_contact_vehicle(
    contact_id: uuid.UUID,
    party_id: uuid.UUID,
    principal: Principal = Depends(require_write("contact_vehicle_links")),
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    party = vehicle_party_reads_service.get_contact_vehicle_or_404(db, contact_id=contact_id, party_id=party_id)
    vehicle_party_service.delete_contact_vehicle(
        db,
        party=party,
        actor_id=principal.user_id,
        acting_dealership_id=principal.tenant_id,
        group_id=principal.group_id,
    )


# --- LegalBasis: revDSG joint-controllership evidence (WP-3 PR-4, ADR-030).
# Recording one is platform_admin-only — it attests a real signed
# joint-controller agreement exists, same authority level as onboarding a
# dealership. This does not itself expose any group-read capability; it
# only supplies the evidence app.contact.services.legal_basis's
# compliance predicate checks before that separate, still-unconsumed path
# would ever return data (see that module's own docstring).


@router.post("/contacts/{contact_id}/legal-basis", response_model=LegalBasisRead, status_code=201)
def create_legal_basis(
    contact_id: uuid.UUID,
    body: LegalBasisCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    contact_common_service.get_contact_or_404(db, principal.group_id, contact_id)
    row = legal_basis_service.record_legal_basis(
        db,
        contact_id=contact_id,
        group_id=principal.group_id,
        basis=body.basis,
        scope=body.scope,
        source_document=body.source_document,
        actor_id=principal.user_id,
    )
    return LegalBasisRead.model_validate(row, from_attributes=True)
