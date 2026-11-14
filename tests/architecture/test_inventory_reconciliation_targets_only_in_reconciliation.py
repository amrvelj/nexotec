"""Architecture test (KAN-123 step 3, ADR-162): `inventory.public` builds what
another context's nightly reconciliation checks against Stock's `stock_item`
table — `stock_item_reference_check`, `stock_item_exists`,
`stock_item_reservation_exists`, `stock_item_prices_moved`,
`stock_item_differs_from_holding`, `purchased_stock_item_check`,
`held_stock_item_check` — so the mapped `StockItem` class never leaves the
inventory context.

A stock item is Betrieb-owned. `stock_item_exists` without a Betrieb answers
whether ANY Betrieb holds an item with that id, and each builder is an EXISTS
or a check another context could embed in a live query, out of
`test_no_cross_context_orm_query.py`'s sight (the model is named inside
inventory). So this test confines their callers to the inventory context and
the `reconciliation.py` modules, which only report (rule 10, ADR-107). A new
caller is a new cross-tenant read: an ADR plus an entry here.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

_BUILDERS = (
    "stock_item_reference_check",
    "stock_item_exists",
    "stock_item_reservation_exists",
    "stock_item_prices_moved",
    "stock_item_differs_from_holding",
    "purchased_stock_item_check",
    "held_stock_item_check",
)


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/inventory/") or relative.endswith("/reconciliation.py")


def _callers_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [
        path
        for path, text in sorted(sources.items())
        if any(name in text for name in _BUILDERS) and not _may_use_them(path)
    ]


def test_the_inventory_builders_are_used_only_by_reconciliation():
    offenders = _callers_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "inventory.public's reconciliation builders read across Betriebe and are for "
        "reconciliation only (KAN-123 step 3, ADR-162); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_builders_are_still_used_by_the_three_reconciliation_modules():
    # Not vacuous: the callers this test exists for are still there.
    sales = (_APP / "sales" / "reconciliation.py").read_text()
    customer = (_APP / "customer" / "reconciliation.py").read_text()
    vehicle = (_APP / "vehicle" / "reconciliation.py").read_text()
    assert all(
        name in sales
        for name in (
            "stock_item_reference_check",
            "stock_item_exists",
            "stock_item_reservation_exists",
            "stock_item_prices_moved",
            "purchased_stock_item_check",
        )
    )
    assert "stock_item_reference_check" in customer
    assert all(
        name in vehicle
        for name in ("stock_item_reference_check", "stock_item_differs_from_holding", "held_stock_item_check")
    )


def test_the_guard_reports_a_caller_outside_reconciliation():
    # Red before green on synthetic sources: a service and an API module are
    # reported; the inventory context and a reconciliation module are not.
    uses = "from app.inventory.public import stock_item_exists\n"
    assert _callers_outside_reconciliation({
        "app/sales/services/contract.py": uses,
        "app/vehicle/api/vehicle_mdm.py": "from app.inventory.public import held_stock_item_check\n",
        "app/inventory/services/reconciliation_targets.py": "def stock_item_exists(): ...\n",
        "app/sales/reconciliation.py": uses,
        "app/customer/services/untouched.py": "x = 1\n",
    }) == ["app/sales/services/contract.py", "app/vehicle/api/vehicle_mdm.py"]
