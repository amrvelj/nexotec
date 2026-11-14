"""Architecture test (KAN-123 step 4, ADR-162): `sales.public` builds what
another context's nightly reconciliation checks against Sales'
`sales_contract` table — `contract_reference_check`,
`signed_contract_carries_valuation`, `contract_carries_valuation`,
`contract_changed_since`, `uncarried_contract_carries_valuation`,
`contract_differs_from_projection`, `unprojected_contract_check` — so the
mapped `SalesContract` class never leaves the sales context.

A contract is Betrieb-owned. `contract_changed_since` answers for any
Betrieb's contract with that id, and each builder is an EXISTS or a check
another context could embed in a live query, out of
`test_no_cross_context_orm_query.py`'s sight (the model is named inside
sales). So this test confines their callers to the sales context and the
`reconciliation.py` modules, which only report (rule 10, ADR-107). A new
caller is a new cross-tenant read: an ADR plus an entry here.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

_BUILDERS = (
    "contract_reference_check",
    "signed_contract_carries_valuation",
    "contract_carries_valuation",
    "contract_changed_since",
    "uncarried_contract_carries_valuation",
    "contract_differs_from_projection",
    "unprojected_contract_check",
)


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/sales/") or relative.endswith("/reconciliation.py")


def _callers_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [
        path
        for path, text in sorted(sources.items())
        if any(name in text for name in _BUILDERS) and not _may_use_them(path)
    ]


def test_the_sales_builders_are_used_only_by_reconciliation():
    offenders = _callers_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "sales.public's reconciliation builders read across Betriebe and are for "
        "reconciliation only (KAN-123 step 4, ADR-162); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_builders_are_still_used_by_the_two_reconciliation_modules():
    # Not vacuous: the callers this test exists for are still there.
    valuation = (_APP / "valuation" / "reconciliation.py").read_text()
    inventory = (_APP / "inventory" / "reconciliation.py").read_text()
    assert all(
        name in valuation
        for name in (
            "contract_reference_check",
            "signed_contract_carries_valuation",
            "contract_carries_valuation",
            "contract_changed_since",
            "uncarried_contract_carries_valuation",
        )
    )
    assert all(
        name in inventory
        for name in ("contract_reference_check", "contract_differs_from_projection", "unprojected_contract_check")
    )


def test_the_guard_reports_a_caller_outside_reconciliation():
    # Red before green on synthetic sources: a service and an API module are
    # reported; the sales context and a reconciliation module are not.
    uses = "from app.sales.public import contract_changed_since\n"
    assert _callers_outside_reconciliation({
        "app/valuation/services/valuation.py": uses,
        "app/inventory/api/stock_items.py": "from app.sales.public import contract_carries_valuation\n",
        "app/sales/services/reconciliation_targets.py": "def contract_changed_since(): ...\n",
        "app/inventory/reconciliation.py": uses,
        "app/customer/services/untouched.py": "x = 1\n",
    }) == ["app/inventory/api/stock_items.py", "app/valuation/services/valuation.py"]
