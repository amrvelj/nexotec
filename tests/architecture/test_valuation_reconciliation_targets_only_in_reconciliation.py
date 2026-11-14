"""Architecture test (KAN-123 step 2, ADR-162): `valuation.public` builds what
another context's nightly reconciliation checks against valuation's table —
`valuation_reference_check`, `valuation_exists`, `valuation_not_used`,
`valuation_final_offer_differs` — so the mapped `Valuation` class never leaves
the valuation context.

Valuations are tenant-private (ADR-029). `valuation_exists` without a Betrieb
answers whether a valuation of ANY Betrieb has that id, and each builder is an
EXISTS another context could embed in a live query, out of
`test_no_cross_context_orm_query.py`'s sight (the model is named inside
valuation). So this test confines their callers to the valuation context and
the `reconciliation.py` modules, which only report (rule 10, ADR-107). A new
caller is a new cross-tenant read: an ADR plus an entry here.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

_BUILDERS = (
    "valuation_reference_check",
    "valuation_exists",
    "valuation_not_used",
    "valuation_final_offer_differs",
)


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/valuation/") or relative.endswith("/reconciliation.py")


def _callers_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [
        path
        for path, text in sorted(sources.items())
        if any(name in text for name in _BUILDERS) and not _may_use_them(path)
    ]


def test_the_valuation_builders_are_used_only_by_reconciliation():
    offenders = _callers_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "valuation.public's reconciliation builders read across Betriebe and are for "
        "reconciliation only (KAN-123 step 2, ADR-162); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_builders_are_still_used_by_the_two_reconciliation_modules():
    # Not vacuous: the callers this test exists for are still there.
    sales = (_APP / "sales" / "reconciliation.py").read_text()
    inventory = (_APP / "inventory" / "reconciliation.py").read_text()
    assert all(name in sales for name in ("valuation_reference_check", "valuation_exists", "valuation_not_used"))
    assert all(name in inventory for name in ("valuation_reference_check", "valuation_final_offer_differs"))


def test_the_guard_reports_a_caller_outside_reconciliation():
    # Red before green on synthetic sources: a service and an API module are
    # reported; the valuation context and a reconciliation module are not.
    uses = "from app.valuation.public import valuation_exists\n"
    assert _callers_outside_reconciliation({
        "app/sales/services/trade_in.py": uses,
        "app/inventory/api/stock.py": "from app.valuation.public import valuation_not_used\n",
        "app/valuation/services/reconciliation_targets.py": "def valuation_exists(): ...\n",
        "app/inventory/reconciliation.py": uses,
        "app/sales/services/untouched.py": "x = 1\n",
    }) == ["app/inventory/api/stock.py", "app/sales/services/trade_in.py"]
