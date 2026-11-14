"""Architecture test (KAN-121 review): `platform.public.list_group_read_dealerships`
answers "which Betriebe belong to this dealer group, while its read is on" for
any group id it is given. With those ids a context can read its own rows of a
whole group — a group read (ADR-014, ADR-030), which CLAUDE.md rule 7 allows
only where an ADR names it.

`test_no_ambient_group_read.py` cannot see such a read: the `dealer_group_id`
predicate it looks for lives inside platform's allow-listed module, and the
caller filters on its own `tenant_id`. So, as `customer_in_group` is confined
(KAN-259), this test confines the callers: platform itself and the one read
ADR-055 rules — Stock's group-readable listing and the customer 360's
in-group-stock lookup, both in `app/inventory/services/group_listing.py`.
`list_group_stock_items` authorises the caller (own group only, 404 never
403) before it asks; `get_stock_items_for_vehicles` takes the caller's
`Principal` and asks only for its token's group (KAN-538), so neither can be
pointed at another group.
A new caller is a new group read — an ADR plus an entry here.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"
_NAME = "list_group_read_dealerships"

# Repo-relative path -> the ruling that allows it.
_ALLOWED_CALLERS = {
    "app/inventory/services/group_listing.py": "ADR-055 group-readable stock (KAN-28, KAN-49), ADR-107",
}


def _may_use_it(relative: str) -> bool:
    return relative.startswith("app/platform/") or relative in _ALLOWED_CALLERS


def _unruled_callers(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name the function but may not."""

    return [path for path, text in sorted(sources.items()) if _NAME in text and not _may_use_it(path)]


def test_the_group_read_dealerships_have_only_ruled_callers():
    offenders = _unruled_callers(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        f"platform.public.{_NAME} hands out a dealer group's Betriebe — a group read (rule 7, ADR-014); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_the_allowed_callers_still_call_it():
    # Not vacuous: each ruled caller still exists and still asks platform.
    for path in _ALLOWED_CALLERS:
        assert _NAME in (_REPO_ROOT / path).read_text(), f"{path} no longer calls {_NAME}; drop its entry"


def test_the_guard_reports_an_unruled_caller():
    # Red before green on synthetic sources: a sales service and a customer
    # API module are reported; platform and the ruled Stock module are not.
    uses = f"from app.platform.public import {_NAME}\n"
    assert _unruled_callers({
        "app/sales/services/offer.py": uses,
        "app/customer/api/customers.py": uses,
        "app/platform/organisation/services/group_read_dealerships.py": f"def {_NAME}(): ...\n",
        "app/inventory/services/group_listing.py": uses,
        "app/sales/services/untouched.py": "x = 1\n",
    }) == ["app/customer/api/customers.py", "app/sales/services/offer.py"]
