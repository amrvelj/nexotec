"""Capability checks (WP-2 PR-2, closes G-08 and the RP-1 leak).

`_WRITE_ROLES` used to conflate two different questions — "may this
principal administer the dealership?" and "may this principal write this
particular kind of record?" — into one role tuple per module. This module
answers them separately: CAPABILITY_MATRIX is a direct transcription of the
Dealer Administration PRD's permission matrix (formerly the "Roles &
Permissions" page), and
require_read()/require_write() are the dependency factories every write
endpoint that used to read `_WRITE_ROLES` now depends on instead.

Every row of the Dealer Administration PRD's permission matrix is listed
(KAN-198), whether or not an endpoint guards it yet — the rows and the
registry are held equal by tests/test_permission_matrix_mirrors_prd.py from
a fixture that mirrors the Notion table; a row where the code deliberately
enforces less than the matrix grants declares that in the fixture with a
ticket (today only stock_items, KAN-376). Module entitlements (D-A-04) are
the other half of "access = entitlement × role": require_entitlement below.

Rule 9 on that page: "A capability that appears in a module PRD but not in
this matrix is not shippable." The inverse holds here — CAPABILITY_MATRIX
is the only place this mapping is allowed to live; an endpoint reaching for
its own ad hoc role tuple again is exactly the mistake this module exists
to prevent.
"""

import dataclasses
import enum
import uuid
from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.db import get_db


class ModuleCode(str, enum.Enum):
    """The module entitlements a Betrieb can hold (D-A-04, D-A-10): what
    Nexotec has licensed to it, set by platform_admin. `sales` is one of them
    since D-A-10 — a service-only garage is created without it and is a normal
    configuration, not a degraded one. The vocabulary lives here because the
    gate (`require_entitlement`) is core code; the rows live in platform's
    `module_entitlement` table."""

    SALES = "sales"
    AFTERSALES = "aftersales"
    PARTS = "parts"
    FINANCE = "finance"
    REPORTING = "reporting"


ALL_MODULES: frozenset[str] = frozenset(module.value for module in ModuleCode)


@dataclasses.dataclass(frozen=True)
class Capability:
    # None = every authenticated role within the tenant may read it
    # (the matrix's "Any role" cells). An empty frozenset is different from
    # None: it means no *functional* role grants it — only is_dealer_manager
    # does (the matrix's "manager"-only cells, e.g. dealership_users' read).
    read_roles: frozenset[AccessRole] | None
    write_roles: frozenset[AccessRole]
    # False for the matrix's "Nobody" write cells (audit logs, the derived
    # views, the platform-only rows): append-only or platform-owned, not
    # even the dealer manager. Every other capability defaults to True,
    # matching ADR-026: the manager flag grants write everywhere in its own
    # dealership.
    manager_can_write: bool = True
    # False for the matrix's platform_admin-only read cells (recovery
    # actions, dealer groups, the canonical taxonomy) and for the rows the
    # matrix reserves to "the user himself" (notification preferences):
    # the manager flag does not open them. Everywhere else the manager reads
    # whatever the dealership holds (ADR-049).
    manager_can_read: bool = True


_ANY = None
_MANAGER_ONLY: frozenset[AccessRole] = frozenset()
_S, _AS, _P, _I, _F, _AU = (
    AccessRole.SALES,
    AccessRole.AFTERSALES,
    AccessRole.PARTS,
    AccessRole.INVENTORY,
    AccessRole.FINANCE,
    AccessRole.AUDITOR,
)

# One entry per row of the Dealer Administration PRD's permission matrix
# (A-RULE-6: guards are generated from the matrix, a capability without a row
# is not shippable). tests/fixtures/permission_matrix.json mirrors the Notion
# table row by row and tests/test_permission_matrix_mirrors_prd.py fails when
# this registry and the fixture disagree. `read_roles=None` = "Any role"; an
# empty set = manager only (plus platform_admin, who passes every check);
# `manager_can_write=False` = the matrix's "Nobody" cell.
CAPABILITY_MATRIX: dict[str, Capability] = {
    # --- shipped modules --------------------------------------------------
    "customers": Capability(read_roles=_ANY, write_roles=frozenset({_S, _AS})),
    "vehicle_mdm": Capability(read_roles=_ANY, write_roles=frozenset({_S, _AS, _I})),
    "customer_vehicle_links": Capability(read_roles=_ANY, write_roles=frozenset({_S, _AS})),
    # "Stock and pipeline vehicles": inventory AND sales write (Anto,
    # 2026-10-17, following the matrix; WP-7 PR-1 had inventory only).
    # The matrix row lets `sales` write stock items too, but this one key
    # guards every inventory write endpoint today — purchase booking, the
    # notional-input-tax override, Wagenbuch costs, publishing, reserve and
    # release — whose own rows (stock_acquisition, wagenbuch,
    # marketplace_publishing, stock_reservation) forbid it. The widening
    # lands only when those endpoints are rewired to their own rows (KAN-376,
    # KAN-198 review); until then the enforced set stays inventory-only.
    "stock_items": Capability(read_roles=_ANY, write_roles=frozenset({_I})),
    "stock_acquisition": Capability(read_roles=_ANY, write_roles=frozenset({_I})),
    "stock_condition": Capability(read_roles=_ANY, write_roles=frozenset({_I, _S})),
    # Wagenbuch: append-only via recordCost, never a user write (ADR-029 read scope).
    "wagenbuch": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "marketplace_publishing": Capability(read_roles=_ANY, write_roles=frozenset({_I})),
    # Retired alongside `transaction` itself (ADR-050, WP-8 PR-7) —
    # sales_offers/sales_contracts are its replacement. Kept only because
    # the endpoints still serve GETs.
    "transactions": Capability(read_roles=_ANY, write_roles=frozenset({_S})),
    "sales_offers": Capability(read_roles=_ANY, write_roles=frozenset({_S})),
    "sales_contracts": Capability(read_roles=_ANY, write_roles=frozenset({_S})),
    "contract_lifecycle": Capability(read_roles=_ANY, write_roles=frozenset({_S})),
    # Stock reservation: no direct user write — the sales contract lifecycle only (ADR-047).
    "stock_reservation": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "commercial_figures": Capability(read_roles=_ANY, write_roles=frozenset({_S})),
    "service_orders": Capability(read_roles=frozenset({_AS, _P, _F, _AU}), write_roles=frozenset({_AS})),
    "parts": Capability(read_roles=frozenset({_P, _AS, _AU}), write_roles=frozenset({_P})),
    "invoices": Capability(read_roles=frozenset({_F, _AU}), write_roles=frozenset({_F})),
    # Valuation: read stays "Any role" (Anto, 2026-10-17, keeping today's
    # behaviour; the matrix row is corrected to match).
    "valuations": Capability(read_roles=_ANY, write_roles=frozenset({_S, _I})),
    # C-C / KAN-41 — the configurator's entity, reached from every
    # commercial process: both Sales and Inventory write.
    "configurations": Capability(read_roles=_ANY, write_roles=frozenset({_S, _I})),
    "audit_logs": Capability(read_roles=frozenset({_AU}), write_roles=_MANAGER_ONLY, manager_can_write=False),
    # --- dealer administration (D-A-01…D-A-41) -----------------------------
    "dealership_settings": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),  # Dealership master data
    "dealership_legal_identity": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    "banking_details": Capability(read_roles=frozenset({_F, _AU}), write_roles=_MANAGER_ONLY),
    "locations": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    "publishing_profile": Capability(read_roles=_ANY, write_roles=frozenset({_I})),
    "branding": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    "document_templates": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    # Number ranges: manager before first use, platform_admin only after
    # (A-RULE-3) — the freeze is the service's rule, not a matrix cell.
    "number_ranges": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    # Standard texts: manager, plus a per-category delegation (FR-A-28) —
    # the delegation hook is KAN-199's.
    "standard_texts": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY),
    "labour_rates": Capability(read_roles=frozenset({_AS, _P, _F}), write_roles=_MANAGER_ONLY),
    # Connected accounts: the user himself links (a self check in the
    # endpoint); the manager sees provider/scopes/status and may revoke.
    "connected_accounts": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "standard_text_delegations": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    # UI translations: everyone reads at runtime, only platform_admin writes.
    "ui_translations": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "recovery_actions": Capability(
        read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_read=False, manager_can_write=False
    ),
    # Notification preferences: the user himself, never the manager (self check in the endpoint).
    "notification_preferences": Capability(
        read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_read=False, manager_can_write=False
    ),
    # Send dialog: "any role with write on the document" — the document's
    # own capability gates the send; this row is the dialog itself.
    "customer_send_dialog": Capability(read_roles=_ANY, write_roles=frozenset({_S, _AS, _I, _P, _F})),
    "sending_identities": Capability(read_roles=frozenset({_F}), write_roles=_MANAGER_ONLY),
    "ai_prompts": Capability(read_roles=frozenset({_I, _S}), write_roles=_MANAGER_ONLY),
    "legal_acceptance": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "data_export": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "status_page": Capability(read_roles=_ANY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "configuration_restore": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "dealership_users": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "manager_flag": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    # WP-6 PR-1 — "visible only to users with the dealer manager flag".
    "integration_connections": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY),
    "integration_usage": Capability(read_roles=frozenset({_F}), write_roles=_MANAGER_ONLY, manager_can_write=False),
    # Module entitlements: the manager reads what the Betrieb bought; only platform_admin writes (D-A-04).
    "module_entitlements": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "setup_checklist": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    "support_session_log": Capability(read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_write=False),
    # --- platform_admin only ------------------------------------------------
    "dealer_groups": Capability(
        read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_read=False, manager_can_write=False
    ),
    "canonical_taxonomy": Capability(
        read_roles=_MANAGER_ONLY, write_roles=_MANAGER_ONLY, manager_can_read=False, manager_can_write=False
    ),
}

# Rule 4 (Roles & Permissions): "auditor is read-only at the framework
# level, not per endpoint, so a new endpoint cannot accidentally grant it
# write access." Asserted once here, at import time, rather than trusted to
# hold by convention at every call site that adds a row above.
for _name, _capability in CAPABILITY_MATRIX.items():
    assert AccessRole.AUDITOR not in _capability.write_roles, (
        f"CAPABILITY_MATRIX['{_name}']: auditor must never hold write (Roles & Permissions rule 4)."
    )


def require_read(capability: str):
    """Dependency factory: `Depends(require_read("customers"))`."""

    cap = CAPABILITY_MATRIX[capability]

    def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if AccessRole.PLATFORM_ADMIN in principal.roles:
            return principal
        if cap.read_roles is None or principal.roles & cap.read_roles:
            return principal
        if cap.manager_can_read and principal.is_dealer_manager:
            return principal
        raise ForbiddenError(f"Not permitted to read '{capability}'.")

    return _check


def require_write(capability: str):
    """Dependency factory: `Depends(require_write("customers"))`."""

    cap = CAPABILITY_MATRIX[capability]

    def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if AccessRole.PLATFORM_ADMIN in principal.roles:
            return principal
        if principal.roles & cap.write_roles:
            return principal
        if cap.manager_can_write and principal.is_dealer_manager:
            return principal
        raise ForbiddenError(f"Not permitted to write '{capability}'.")

    return _check


# --- Module entitlements (D-A-04, KAN-198) -------------------------------------------------

# Reads a dealership's current `entitlements_version`, or None when no such
# dealership exists. Registered by platform at import time
# (app.platform.services.entitlement): core never imports a bounded context,
# so the one piece of platform state the gate needs arrives as a callback.
EntitlementVersionReader = Callable[[Session, uuid.UUID], int | None]
_entitlement_version_reader: EntitlementVersionReader | None = None


def set_entitlement_version_reader(reader: EntitlementVersionReader) -> None:
    global _entitlement_version_reader
    _entitlement_version_reader = reader


def require_entitlement(module: ModuleCode):
    """Dependency factory: `APIRouter(dependencies=[Depends(require_entitlement(ModuleCode.SALES))])`.

    The entitlement gate runs before any role check (D-A-04): an unlicensed
    module is 404 for everyone — the manager and platform staff acting for a
    dealer included — so nothing confirms the module exists. The token's
    `entitlements` claim decides; a token minted before the Betrieb's
    entitlements last changed (its `ent_v` differs from the dealership's
    `entitlements_version`) is refused with 401 so the client signs in again
    and receives the current set (Anto, 2026-10-17). A dealership that no
    longer exists has no version to compare against; its token cannot read
    anything tenant-scoped anyway.
    """

    def _check(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)) -> Principal:
        if _entitlement_version_reader is None:
            raise RuntimeError("require_entitlement: no entitlement version reader registered (platform not loaded)")
        current = _entitlement_version_reader(db, principal.tenant_id)
        if current is not None and current != principal.entitlements_version:
            raise UnauthorizedError("Your session is out of date — sign in again.")
        if module.value not in principal.entitlements:
            raise NotFoundError("Not found.")
        return principal

    return _check


# --- Standard-text delegations (FR-A-28, D-A-23, KAN-199) ---------------------------------

# Whether the principal holds a delegation for `category` of `capability`
# (today only "standard_texts") in their active dealership. Registered by
# platform at import time (app.platform.services.text_delegation); core never
# imports a bounded context, so the one lookup the gate needs arrives as a
# callback, like the entitlement version reader above.
DelegationChecker = Callable[[Session, Principal, str, str], bool]
_delegation_checker: DelegationChecker | None = None


def set_delegation_checker(checker: DelegationChecker) -> None:
    global _delegation_checker
    _delegation_checker = checker


def require_write_or_delegated(capability: str, category_param: str = "category"):
    """Dependency factory: `Depends(require_write_or_delegated("standard_texts"))`.

    The matrix's "Standard texts" write row: the manager (and platform staff,
    and any listed write role), PLUS a user holding a per-category delegation
    for that category only (FR-A-28) — the one contained exception to "no
    per-dealership roles". The category is read from the route's PATH
    parameter `category_param` only — never from the query string or the
    body; a route without that path parameter behaves exactly like
    require_write (a delegation opens nothing there). The caller chooses the
    path too, so the endpoint behind this gate must bind what it writes to
    the path's category (look the text up BY category, never by id alone) —
    otherwise a delegate of `email` could save a document text under
    `/…/email/{id}`. 403 when neither holds.
    """

    cap = CAPABILITY_MATRIX[capability]

    def _check(
        request: Request,
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ) -> Principal:
        if AccessRole.PLATFORM_ADMIN in principal.roles:
            return principal
        if principal.roles & cap.write_roles:
            return principal
        if cap.manager_can_write and principal.is_dealer_manager:
            return principal
        category = request.path_params.get(category_param)
        if (
            category is not None
            and _delegation_checker is not None
            and _delegation_checker(db, principal, capability, str(category))
        ):
            return principal
        raise ForbiddenError(f"Not permitted to write '{capability}'.")

    return _check
