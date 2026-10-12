"""Architecture test (KAN-259 review): `customer.public.customer_in_group`
is a group-scoped predicate — "this customer belongs to that dealer group"
— built for nightly reconciliation (CLAUDE.md rule 10) to report an offer or
contract naming a customer outside its dealership's group (ADR-014).

Any context could call it with any group id, and `test_no_ambient_group_read.py`
cannot see such a call (the `group_id ==` it looks for lives inside the
customer context, which is allow-listed). So this test confines its callers:
the customer context itself and the `reconciliation.py` modules, which only
report. A new caller is a new group read — an ADR plus an entry here.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"


def _may_use_it(relative: str) -> bool:
    return relative.startswith("app/customer/") or relative.endswith("/reconciliation.py")


def _callers_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name the predicate but may not."""

    return [path for path, text in sorted(sources.items()) if "customer_in_group" in text and not _may_use_it(path)]


def test_customer_in_group_is_used_only_by_reconciliation():
    offenders = _callers_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "customer.public.customer_in_group is a group read for reconciliation only (KAN-259); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_predicate_is_still_used_by_sales_reconciliation():
    # Not vacuous: the one caller this test exists for is still there.
    assert "customer_in_group" in (_APP / "sales" / "reconciliation.py").read_text()


def test_the_guard_reports_a_caller_outside_reconciliation():
    # Red before green on synthetic sources: a service and an API module are
    # reported; the customer context and a reconciliation module are not.
    uses = "from app.customer.public import customer_in_group\n"
    assert _callers_outside_reconciliation({
        "app/sales/services/offer.py": uses,
        "app/inventory/api/stock.py": uses,
        "app/customer/services/customer.py": "def customer_in_group(): ...\n",
        "app/valuation/reconciliation.py": uses,
        "app/sales/services/untouched.py": "x = 1\n",
    }) == ["app/inventory/api/stock.py", "app/sales/services/offer.py"]
