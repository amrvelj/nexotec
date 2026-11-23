"""Architecture test (KAN-123 step 7, ADR-162): since `DealerGroup`,
`Dealership`, `Location` and `User` left `platform.public`, platform builds
what every other context's nightly reconciliation checks against its
`dealer_group`, `dealership`, `location` and `user` tables
(`app/platform/organisation/services/reconciliation_targets.py`,
`app/platform/identity/services/reconciliation_targets.py`, and since KAN-795
`app/platform/documents/services/reconciliation_targets.py` for `document_link`).

Each builder is a ReferenceCheck, an EXISTS or a scalar subquery over
platform's tables, or a plain read of them by id; none is scoped to the
caller's Betrieb. Embedded in a live query or called from a service, it
would read across out of `test_no_cross_context_orm_query.py`'s and the
tenant filter's sight — "does this Betrieb, this user, exist?" for any id.
So this test confines its users: the platform context itself and the
`reconciliation.py` modules, which only report. A new caller is an ADR plus
an entry here. (`actor_reference_checks` predates this and is not
confined: `test_actor_columns_reconciled.py` governs it.)
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

_BUILDERS = (
    "dealer_group_reference_check",
    "dealership_exists",
    "dealership_group_of",
    "dealership_outside_group",
    "dealership_reference_check",
    "document_link_reference_check",
    "find_dealer_group_ids",
    "find_dealership_group_ids",
    "location_reference_check",
    "user_is_deactivated",
    "user_reference_check",
)

_NAMED = re.compile(r"\b(" + "|".join(_BUILDERS) + r")\b")

# Where each builder's caller is — the test below proves it is still there.
_CALLERS = ("customer", "sales", "valuation", "integration", "inventory", "vehicle", "reporting")


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/platform/") or relative.endswith("/reconciliation.py")


def _users_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [path for path, text in sorted(sources.items()) if _NAMED.search(text) and not _may_use_them(path)]


def test_the_platform_builders_are_used_only_by_reconciliation():
    offenders = _users_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "platform.public's reconciliation builders are for reconciliation only (KAN-123 step 7, ADR-162); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_every_builder_is_still_used_by_a_reconciliation_module():
    # Not vacuous: each builder this test exists for has its caller.
    text = "".join((_APP / context / "reconciliation.py").read_text() for context in _CALLERS)
    assert [name for name in _BUILDERS if f"{name}(" not in text] == []


def test_the_guard_reports_a_user_outside_reconciliation():
    # Red before green on synthetic sources.
    assert _users_outside_reconciliation({
        "app/sales/services/offer.py": "from app.platform.public import dealership_exists\n",
        "app/inventory/api/stock.py": "from app.platform.public import user_is_deactivated\n",
        "app/customer/services/ownership.py": "known = find_dealer_group_ids(db, ids)\n",
        "app/vehicle/services/plate.py": "q = select(X).where(dealership_outside_group(a, b))\n",
        "app/integration/services/connection.py": "location_reference_check(label='x')\n",
        "app/platform/organisation/services/reconciliation_targets.py": "def dealership_exists(): ...\n",
        "app/platform/reconciliation.py": "user_reference_check(label='x')\n",
        "app/valuation/reconciliation.py": "from app.platform.public import dealership_reference_check\n",
        "app/sales/services/untouched.py": "x = 1\n",
        # A longer name, or a local variable that merely contains one, is not a use.
        "app/sales/services/deal_list.py": "dealership_exists_for_tenant = True\n",
        "app/customer/services/customer.py": "def find_dealer_group_ids_cached(): ...\n",
    }) == [
        "app/customer/services/ownership.py",
        "app/integration/services/connection.py",
        "app/inventory/api/stock.py",
        "app/sales/services/offer.py",
        "app/vehicle/services/plate.py",
    ]
