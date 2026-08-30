"""Capability checks (WP-2 PR-2, closes G-08 and the RP-1 leak).

`_WRITE_ROLES` used to conflate two different questions — "may this
principal administer the dealership?" and "may this principal write this
particular kind of record?" — into one role tuple per module. This module
answers them separately: CAPABILITY_MATRIX is a direct transcription of the
Notion "Roles & Permissions" page's permission-matrix table, and
require_read()/require_write() are the dependency factories every write
endpoint that used to read `_WRITE_ROLES` now depends on instead.

Only capabilities with a shipped endpoint to guard are listed — the
aftersales/parts/finance rows in the Notion matrix have no code yet.

Rule 9 on that page: "A capability that appears in a module PRD but not in
this matrix is not shippable." The inverse holds here — CAPABILITY_MATRIX
is the only place this mapping is allowed to live; an endpoint reaching for
its own ad hoc role tuple again is exactly the mistake this module exists
to prevent.
"""

import dataclasses

from fastapi import Depends

from app.core.auth import AccessRole, Principal, get_current_principal
from app.core.errors import ForbiddenError


@dataclasses.dataclass(frozen=True)
class Capability:
    # None = every authenticated role within the tenant may read it
    # (the matrix's "Any role" cells). An empty frozenset is different from
    # None: it means no *functional* role grants it — only is_dealer_manager
    # does (the matrix's "manager"-only cells, e.g. dealership_users' read).
    read_roles: frozenset[AccessRole] | None
    write_roles: frozenset[AccessRole]
    # False only for audit_logs: append-only by the system, and the matrix
    # is explicit that this is "Nobody — append-only by the system", not
    # even the dealer manager. Every other capability defaults to True,
    # matching ADR-026: the manager flag grants write everywhere in its own
    # dealership.
    manager_can_write: bool = True


CAPABILITY_MATRIX: dict[str, Capability] = {
    "customers": Capability(
        read_roles=None, write_roles=frozenset({AccessRole.SALES, AccessRole.AFTERSALES})
    ),
    "vehicle_mdm": Capability(
        read_roles=None,
        write_roles=frozenset({AccessRole.SALES, AccessRole.AFTERSALES, AccessRole.INVENTORY}),
    ),
    # WP-7 PR-1: only inventory writes a stock item directly. Sales never
    # writes one — it calls the inventory.public reserve/release surface
    # (PR-4) instead, which is inventory's own commit, not a write through
    # this capability.
    "stock_items": Capability(read_roles=None, write_roles=frozenset({AccessRole.INVENTORY})),
    "customer_vehicle_links": Capability(
        read_roles=None, write_roles=frozenset({AccessRole.SALES, AccessRole.AFTERSALES})
    ),
    # Retired alongside `transaction` itself (ADR-050, WP-8 PR-7) —
    # `sales_offers`/`sales_contracts` below are its replacement. Kept
    # (read-only in practice once PR-7 lands) only because the endpoints
    # still serve GETs.
    "transactions": Capability(read_roles=None, write_roles=frozenset({AccessRole.SALES})),
    # WP-8 PR-1: real offers and contracts (sales_offer/sales_contract),
    # ADR-050's replacement for "transactions" above.
    "sales_offers": Capability(read_roles=None, write_roles=frozenset({AccessRole.SALES})),
    "sales_contracts": Capability(read_roles=None, write_roles=frozenset({AccessRole.SALES})),
    "dealership_settings": Capability(read_roles=None, write_roles=frozenset()),
    # WP-6b, ADR-044 tier 2: document templates (letterhead/branding/
    # boilerplate) are edited by platform staff and dealer managers, same
    # shape as dealership_settings — no functional role grants write on its
    # own, only the manager flag does.
    "document_templates": Capability(read_roles=None, write_roles=frozenset()),
    "dealership_users": Capability(read_roles=frozenset(), write_roles=frozenset()),
    "audit_logs": Capability(
        read_roles=frozenset({AccessRole.AUDITOR}), write_roles=frozenset(), manager_can_write=False
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
        if principal.is_dealer_manager:
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
