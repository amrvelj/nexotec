"""Dealership + User bootstrap endpoints (issue #2, renamed WP-3).

POST /v1/dealerships is platform-admin only — it's how the first tenant gets
created. POST /v1/dealerships/{id}/users creates the initial admin User for
that Dealership (also usable by an existing dealer manager to add staff
within their own tenant). See PLANS/DMS_MDM_V1_SPEC.md §3 + the Swiss
addendum.

Dealership settings and dealership-user management (WP-2 PR-2) are manager-
only in both directions now — Roles & Permissions' matrix marks "Users of
the dealership" read as `manager`, not "Any role" the way most of the
matrix is, so this tightens list_users/get_user, not just the writes that
used to read _WRITE_ROLES.

KAN-668: this module keeps the dealership itself (CRUD, bank accounts,
settings, audit log, entitlements). The user routes are in
`dealership_users.py`, memberships, the manager flag and text delegations in
`dealership_memberships.py`, invitations, connected accounts, auth events and
the auth config in `dealership_identity.py` — same paths, same tag.
"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.audit import list_tenant_audit_events
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.pagination import PageParams, page_params
from app.core.permissions import (
    ModuleCode,
    require_banking_read,
    require_banking_write,
    require_read,
    require_write,
)
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.dealership import DealershipStatus
from app.platform.schemas.bank_account import BankAccountCreate, BankAccountList, BankAccountRead, BankAccountUpdate
from app.platform.schemas.dealership import DealershipCreate, DealershipPage, DealershipRead, DealershipUpdate
from app.platform.schemas.dealership_settings import (
    AddressAutocompleteConfigRead,
    DealershipSettingsRead,
    DealershipSettingsUpdate,
)
from app.platform.schemas.entitlement import EntitlementsRead, EntitlementsUpdate
from app.platform.services import bank_account as bank_account_service
from app.platform.services import dealership as dealership_service
from app.platform.services import dealership_settings as settings_service
from app.platform.services import entitlement as entitlement_service

router = APIRouter(tags=["dealerships"], route_class=IdempotentRoute)


# --- Dealership ------------------------------------------------------------


@router.post(
    "/dealerships",
    response_model=DealershipRead,
    status_code=201,
    deprecated=True,
    description="Deprecated (KAN-210): onboard a Betrieb with POST /v1/platform/onboarding — group, legal entity, "
    "entitlements and first manager in one transaction. Kept for API clients.",
)
def create_dealership(
    body: DealershipCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    dealership = dealership_service.create_dealership(db, data=body, actor_id=principal.user_id)
    return DealershipRead.model_validate(dealership, from_attributes=True)


@router.get("/dealerships/{dealership_id}", response_model=DealershipRead)
def get_dealership(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return DealershipRead.model_validate(dealership, from_attributes=True)


@router.patch("/dealerships/{dealership_id}", response_model=DealershipRead)
def update_dealership(
    dealership_id: uuid.UUID,
    body: DealershipUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    if body.touches_legal_identity():
        # KAN-203: the matrix's own row for legal name, UID and VAT — the same
        # writers today (the manager), checked by name so the row stays real.
        require_write("dealership_legal_identity")(principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    dealership = dealership_service.update_dealership(
        db,
        dealership=dealership,
        data=body,
        actor_id=principal.user_id,
        # KAN-712: only staff may name `status`, and only to suspend.
        actor_is_staff=AccessRole.PLATFORM_ADMIN in principal.roles,
    )
    return DealershipRead.model_validate(dealership, from_attributes=True)


# --- Business master data: bank accounts, fiscal defaults (KAN-203) ----------
#
# Banking is the one capability whose refusal is a 404 (ADR-100, Anto
# 2026-10-24): outside manager, finance, auditor and platform staff the
# accounts do not exist. Reads and writes alike.


def _bank_account_read(account) -> BankAccountRead:
    return BankAccountRead.model_validate(account, from_attributes=True)


@router.get("/dealerships/{dealership_id}/bank-accounts", response_model=BankAccountList)
def list_bank_accounts(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_banking_read()),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    rows = bank_account_service.list_accounts(db, dealership_id=dealership_id)
    return BankAccountList(items=[_bank_account_read(row) for row in rows])


@router.post("/dealerships/{dealership_id}/bank-accounts", response_model=BankAccountRead, status_code=201)
def create_bank_account(
    dealership_id: uuid.UUID,
    body: BankAccountCreate,
    principal: Principal = Depends(require_banking_write()),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    account = bank_account_service.create_account(db, dealership_id=dealership_id, data=body, actor_id=principal.user_id)
    return _bank_account_read(account)


@router.get("/dealerships/{dealership_id}/bank-accounts/{account_id}", response_model=BankAccountRead)
def get_bank_account(
    dealership_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: Principal = Depends(require_banking_read()),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    account = bank_account_service.get_account_or_404(db, dealership_id=dealership_id, account_id=account_id)
    return _bank_account_read(account)


@router.patch("/dealerships/{dealership_id}/bank-accounts/{account_id}", response_model=BankAccountRead)
def update_bank_account(
    dealership_id: uuid.UUID,
    account_id: uuid.UUID,
    body: BankAccountUpdate,
    # The banking guard runs before the If-Match check (dependencies run in
    # declaration order): a role outside banking gets the 404 even without
    # the header, never a 400 that reads differently (ADR-100).
    principal: Principal = Depends(require_banking_write()),
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    account = bank_account_service.get_account_or_404(
        db, dealership_id=dealership_id, account_id=account_id, for_update=True
    )
    check_version(account.version, if_match, entity_name="DealershipBankAccount")
    account = bank_account_service.update_account(db, account=account, data=body, actor_id=principal.user_id)
    return _bank_account_read(account)


@router.delete("/dealerships/{dealership_id}/bank-accounts/{account_id}", status_code=204)
def delete_bank_account(
    dealership_id: uuid.UUID,
    account_id: uuid.UUID,
    principal: Principal = Depends(require_banking_write()),  # before If-Match, as on the PATCH (ADR-100)
    if_match: int = Depends(require_if_match),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    account = bank_account_service.get_account_or_404(
        db, dealership_id=dealership_id, account_id=account_id, for_update=True
    )
    check_version(account.version, if_match, entity_name="DealershipBankAccount")
    bank_account_service.delete_account(db, account=account, actor_id=principal.user_id)
    return Response(status_code=204)


def _settings_read(settings) -> DealershipSettingsRead:
    return DealershipSettingsRead.model_validate(settings, from_attributes=True)


@router.get("/dealerships/{dealership_id}/settings", response_model=DealershipSettingsRead)
def get_dealership_settings(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    """The commercial and fiscal defaults every document inherits (FR-A-25) —
    readable by any role of the Betrieb, like the rest of the master data."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    return _settings_read(settings_service.read_settings(db, dealership_id))  # a read never writes


@router.patch("/dealerships/{dealership_id}/settings", response_model=DealershipSettingsRead)
def update_dealership_settings(
    dealership_id: uuid.UUID,
    body: DealershipSettingsUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("dealership_settings")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    settings_row = settings_service.settings_for(db, dealership_id, for_update=True)
    check_version(settings_row.version, if_match, entity_name="DealershipSettings")
    settings_row = settings_service.update_settings(db, settings=settings_row, data=body, actor_id=principal.user_id)
    return _settings_read(settings_row)


@router.get("/dealerships/{dealership_id}/address-autocomplete", response_model=AddressAutocompleteConfigRead)
def get_address_autocomplete_config(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("dealership_settings")),
    db: Session = Depends(get_db),
):
    """KAN-37 (ADR-101): whether this Betrieb's address forms offer Google
    Places Autocomplete, and the browser key when they do. Readable by any
    role of the Betrieb (every role enters addresses); the switch itself is
    `addressAutocompleteEnabled` on the settings PATCH, the manager's."""

    require_tenant_match(dealership_id, principal)
    dealership_service.get_dealership_or_404(db, dealership_id)
    enabled, browser_key = settings_service.address_autocomplete_config(db, dealership_id)
    return AddressAutocompleteConfigRead(enabled=enabled, browser_key=browser_key)


@router.get("/dealerships", response_model=DealershipPage)
def list_dealerships(
    status: DealershipStatus | None = None,
    dealer_group_id: uuid.UUID | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    rows, next_cursor = dealership_service.list_dealerships(
        db, params=params, status=status, dealer_group_id=dealer_group_id
    )
    return DealershipPage(
        items=[DealershipRead.model_validate(d, from_attributes=True) for d in rows], next_cursor=next_cursor
    )


@router.get("/dealerships/{dealership_id}/audit-log", response_model=AuditEventPage)
def get_dealership_audit_log(
    dealership_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_read("audit_logs")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    rows, next_cursor = list_tenant_audit_events(db, tenant_id=dealership_id, params=params)
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in rows], next_cursor=next_cursor
    )


# --- Module entitlements (KAN-198, D-A-04) ----------------------------------


def _entitlements_read(db: Session, dealership) -> EntitlementsRead:
    modules = entitlement_service.list_active_modules(db, dealership.id)
    return EntitlementsRead(
        modules=sorted((ModuleCode(m) for m in modules), key=lambda m: m.value),
        entitlements_version=dealership.entitlements_version,
        dealership_version=dealership.version,
    )


@router.get("/dealerships/{dealership_id}/entitlements", response_model=EntitlementsRead)
def get_entitlements(
    dealership_id: uuid.UUID,
    principal: Principal = Depends(require_read("module_entitlements")),  # manager reads "what we bought"
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    dealership = dealership_service.get_dealership_or_404(db, dealership_id)
    return _entitlements_read(db, dealership)


@router.put("/dealerships/{dealership_id}/entitlements", response_model=EntitlementsRead)
def set_entitlements(
    dealership_id: uuid.UUID,
    body: EntitlementsUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("module_entitlements")),  # platform_admin only (D-A-04)
    db: Session = Depends(get_db),
):
    dealership = dealership_service.get_dealership_for_update_or_404(db, dealership_id)
    check_version(dealership.version, if_match, entity_name="Dealership")
    entitlement_service.set_entitlements(
        db, dealership=dealership, modules=set(body.modules), actor_id=principal.user_id
    )
    return _entitlements_read(db, dealership)
