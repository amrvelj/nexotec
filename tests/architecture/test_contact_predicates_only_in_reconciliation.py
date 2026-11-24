"""Architecture test (KAN-259 review): `contact.public.contact_in_group`
is a group-scoped predicate — "this contact belongs to that dealer group"
— built for nightly reconciliation (CLAUDE.md rule 10) to report an offer or
contract naming a contact outside its dealership's group (ADR-014).

Any context could call it with any group id, and `test_no_ambient_group_read.py`
cannot see such a call (the `group_id ==` it looks for lives inside the
contact context, which is allow-listed). So this test confines its callers:
the contact context itself and the `reconciliation.py` modules, which only
report. A new caller is a new group read — an ADR plus an entry here.

`contact.public.contact_is_merged` (KAN-581) is the same kind of builder —
an EXISTS over `contact` another context could embed in a live query, out of
`test_no_cross_context_orm_query.py`'s sight — and is confined the same way.
So are `contact_exists` and `contact_reference_check` (KAN-123 step 5,
ADR-162): what Sales', Valuation's and Stock's reconciliation check against
`contact` since `Contact` left `contact.public`.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"


def _may_use_it(relative: str) -> bool:
    return relative.startswith("app/contact/") or relative.endswith("/reconciliation.py")


_PREDICATES = ("contact_in_group", "contact_is_merged", "contact_exists", "contact_reference_check")


def _callers_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a predicate but may not."""

    return [
        path
        for path, text in sorted(sources.items())
        if any(name in text for name in _PREDICATES) and not _may_use_it(path)
    ]


def test_the_contact_predicates_are_used_only_by_reconciliation():
    offenders = _callers_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "contact.public.contact_in_group / contact_is_merged are reads for reconciliation only "
        "(KAN-259, KAN-581); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_predicate_is_still_used_by_sales_reconciliation():
    # Not vacuous: the one caller this test exists for is still there.
    text = (_APP / "sales" / "reconciliation.py").read_text()
    assert all(name in text for name in _PREDICATES)


def test_the_guard_reports_a_caller_outside_reconciliation():
    # Red before green on synthetic sources: a service and an API module are
    # reported; the contact context and a reconciliation module are not.
    uses = "from app.contact.public import contact_in_group\n"
    assert _callers_outside_reconciliation({
        "app/sales/services/offer.py": uses,
        "app/inventory/api/stock.py": uses,
        "app/contact/services/contact.py": "def contact_in_group(): ...\n",
        "app/valuation/reconciliation.py": uses,
        "app/sales/services/untouched.py": "x = 1\n",
        "app/valuation/services/valuation.py": "from app.contact.public import contact_is_merged\n",
        # KAN-123 step 5: the reference builders are confined the same way.
        "app/inventory/services/stock_item.py": "from app.contact.public import contact_reference_check\n",
        "app/sales/services/deal_list.py": "from app.contact.public import contact_exists\n",
    }) == [
        "app/inventory/api/stock.py",
        "app/inventory/services/stock_item.py",
        "app/sales/services/deal_list.py",
        "app/sales/services/offer.py",
        "app/valuation/services/valuation.py",
    ]
