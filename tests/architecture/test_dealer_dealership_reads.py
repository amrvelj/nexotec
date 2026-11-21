"""Architecture test (KAN-221 PR-1): the platform tenant is never read as a dealer.

The platform tenant is a `dealership` row of kind `platform` (the Betrieb
«Nexotec» homing Nexotec's staff) in its own group. A query that lists
Betriebe or dealer groups for a dealer purpose — a nightly run over every
Betrieb, a picker, a group read, an export — must leave it out, through
`app/platform/organisation/services/dealer_dealerships.py`'s
`dealer_dealerships_only()` / `dealer_groups_only()`.

This test parses every function in `app/` and `scripts/` that calls
`select(…)`, `join(…)`, `outerjoin(…)` or `join_from(…)` with `Dealership` or
`DealerGroup` (the model or one of its columns) among the arguments. Each
such function either calls one of the two clauses or is named below with the
reason the platform tenant cannot reach it or may. A new query over
Betriebe fails here until it decides.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODELS = frozenset({"Dealership", "DealerGroup"})
_CLAUSES = frozenset({"dealer_dealerships_only", "dealer_groups_only"})
_QUERY_CALLS = frozenset({"select", "join", "outerjoin", "join_from"})

_BY_ID = "reads one Betrieb (or group) by its id — a lookup, not a listing; the platform tenant is a real row"
_REFERENCE = "a reconciliation reference check by id — the platform tenant is a real row users and audit rows name"

# "path::function" -> why it reads Betriebe without the dealer clause.
_ALLOWED: dict[str, str] = {
    "app/platform/identity/services/membership.py::list_memberships": (
        "a user's own memberships; grant_membership refuses the platform tenant as a target (KAN-221 PR-1)"
    ),
    "app/platform/documents/services/document_links.py::open_document_link": _BY_ID,
    "app/platform/identity/services/onboarding.py::onboard": (
        "the UID duplicate check; the platform tenant has no UID"
    ),
    "app/platform/identity/services/recovery.py::account_states": (
        "the names of the found users' own home Betriebe — staff's home is the platform tenant, and says so"
    ),
    "app/platform/identity/services/platform_staff.py::entries_for": (
        "the names of the listed staff members' own home Betriebe, by id — the platform tenant for staff, a "
        "dealer's Betrieb for a legacy holder (KAN-221 PR-6), and says which"
    ),
    "app/platform/identity/services/support_sessions.py::list_support_sessions": (
        "the names of the Betriebe the listed support sessions ran in, by id; a session never targets the "
        "platform tenant (support_target_platform_tenant)"
    ),
    "app/platform/identity/services/user_preference.py::default_ui_language": _BY_ID,
    "app/platform/reconciliation.py::_platform_admin_homed_outside_the_platform_tenant": (
        "selects the platform tenant itself: a platform_admin homed anywhere else is the finding (KAN-764)"
    ),
    "app/platform/identity/services/user_reads.py::list_user_dealer_group_ids": (
        "the groups of one user's own Betriebe, by id; the Nexotec group holds no customers or records"
    ),
    "app/platform/identity/services/user_reads.py::_other_dealership_names": (
        "a user's active memberships; grant_membership refuses the platform tenant as a target"
    ),
    "app/platform/identity/session_guard.py::enforce_open_dealership": _BY_ID,
    "app/platform/organisation/services/contact_group.py::set_contact_group_enabled": _BY_ID + " (a row lock)",
    "app/platform/organisation/services/dealership.py::get_dealership_for_update_or_404": _BY_ID,
    "app/platform/organisation/services/dealership.py::list_dealerships_by_ids": (
        "a user's home and memberships by id, for the session's Betrieb list — staff's home is the platform tenant"
    ),
    "app/platform/organisation/services/entitlement.py::read_entitlements_version": _BY_ID,
    "app/platform/organisation/services/reconciliation_targets.py::dealership_exists": _REFERENCE,
    "app/platform/organisation/services/reconciliation_targets.py::dealership_group_of": _REFERENCE,
    "app/platform/organisation/services/reconciliation_targets.py::dealership_outside_group": _REFERENCE,
    "app/platform/organisation/services/reconciliation_targets.py::find_dealer_group_ids": _REFERENCE,
    "app/platform/organisation/services/reconciliation_targets.py::find_dealership_group_ids": _REFERENCE,
    "scripts/migrate_logo_urls_to_branding_assets.py::migrate": (
        "Betriebe with an old logo_url; the platform tenant never had one"
    ),
    "scripts/retire_staging_shared_demo_user.py::run": "the staging demo Betrieb, by its legal name",
    "scripts/seed_staging_demo.py::_ensure_dealership": "the staging demo Betrieb, by its legal name",
    "scripts/verify_migration_smoke_test_seed.py::main": (
        "the smoke test's own Betriebe — counted by kind and by legal name (a CI fixture, ADR-140: it imports no service)"
    ),
}


def _names_a_model(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in _MODELS:
            return True
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in _MODELS:
            return True
    return False


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def queries_over_betriebe(source: str) -> list[tuple[str, bool]]:
    """(function name, applies a dealer clause) for every function of `source`
    whose own body queries `Dealership` or `DealerGroup`."""

    found = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        queries = any(
            _call_name(call) in _QUERY_CALLS and any(_names_a_model(arg) for arg in call.args) for call in calls
        )
        if queries:
            found.append((function.name, any(_call_name(call) in _CLAUSES for call in calls)))
    return found


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(_REPO_ROOT)): path.read_text()
        for root in ("app", "scripts")
        for path in sorted((_REPO_ROOT / root).rglob("*.py"))
    }


def _unfiltered() -> set[str]:
    return {
        f"{path}::{name}"
        for path, text in _sources().items()
        for name, filtered in queries_over_betriebe(text)
        if not filtered
    }


def test_every_query_over_betriebe_leaves_the_platform_tenant_out_or_says_why():
    offenders = sorted(_unfiltered() - set(_ALLOWED))
    assert offenders == [], (
        "These functions query Dealership/DealerGroup without dealer_dealerships_only() / dealer_groups_only() "
        "(app/platform/organisation/services/dealer_dealerships.py). Apply the clause, or — for a lookup by id — "
        f"name the function with its reason in this test's allow-list (KAN-221): {offenders}"
    )


def test_the_allow_list_holds_no_stale_entry():
    stale = sorted(set(_ALLOWED) - _unfiltered())
    assert stale == [], f"These allow-list entries no longer query Betriebe unfiltered — drop them: {stale}"


def test_the_scan_sees_an_unfiltered_listing_and_a_filtered_one():
    # Not vacuous: the parser finds both shapes.
    source = (
        "def bad(db):\n    return db.scalars(select(Dealership.id).where(Dealership.status == 'active'))\n"
        "def good(db):\n    return db.scalars(select(Dealership).where(dealer_dealerships_only()))\n"
        "def groups(db):\n    return db.scalars(select(DealerGroup).where(dealer_groups_only()))\n"
        "def joined(db):\n    return select(User.id).join(Dealership, Dealership.id == User.tenant_id)\n"
        "def other(db):\n    return select(User)\n"
    )
    assert queries_over_betriebe(source) == [("bad", False), ("good", True), ("groups", True), ("joined", False)]
