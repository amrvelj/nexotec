"""Architecture test (KAN-123 step 6, ADR-162): since `VehicleMdm` and
`VehicleConfiguration` left `vehicle.public`, Vehicle builds what Contacts',
Sales', Valuation's and Stock's nightly reconciliation checks against its
`vehicle_mdm` and `vehicle_configuration` tables
(`app/vehicle/services/reconciliation_targets.py`).

Each builder is an EXISTS or a ReferenceCheck over Vehicle's tables; embedded
in a live query, it would read across out of `test_no_cross_context_orm_query.py`'s
sight. So this test confines its users: the vehicle context itself and the
`reconciliation.py` modules, which only report. A new caller is an ADR plus
an entry here.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

_BUILDERS = (
    "configuration_exists",
    "configuration_reference_check",
    "configuration_untaken",
    "configuration_vin_distinct_from",
    "vehicle_is_merged",
    "vehicle_reference_check",
    "vehicle_vin_differs",
    "vehicle_vin_distinct_from",
)


_NAMED = re.compile(r"\b(" + "|".join(_BUILDERS) + r")\b")


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/vehicle/") or relative.endswith("/reconciliation.py")


def _users_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [
        path
        for path, text in sorted(sources.items())
        if _NAMED.search(text) and not _may_use_them(path)
    ]


def test_the_vehicle_builders_are_used_only_by_reconciliation():
    offenders = _users_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "vehicle.public's reconciliation builders are for reconciliation only (KAN-123 step 6, ADR-162); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_every_builder_is_still_used_by_a_reconciliation_module():
    # Not vacuous: each builder this test exists for has its caller.
    text = "".join(
        (_APP / context / "reconciliation.py").read_text() for context in ("customer", "sales", "valuation", "inventory")
    )
    assert [name for name in _BUILDERS if f"{name}(" not in text] == []


def test_the_guard_reports_a_user_outside_reconciliation():
    # Red before green on synthetic sources.
    assert _users_outside_reconciliation({
        "app/sales/services/offer.py": "from app.vehicle.public import configuration_untaken\n",
        "app/inventory/api/stock.py": "from app.vehicle.public import vehicle_is_merged\n",
        "app/contact/services/vehicle_party.py": "vehicle_reference_check(label='x')\n",
        "app/vehicle/services/reconciliation_targets.py": "def vehicle_is_merged(): ...\n",
        "app/valuation/reconciliation.py": "from app.vehicle.public import vehicle_vin_differs\n",
        "app/sales/services/untouched.py": "x = 1\n",
        # A longer name, or the merge's reason code, is not a use.
        "app/inventory/consumers.py": "def handle_vehicle_merged(db, message): ...\n",
        "app/inventory/services/vehicle_merge.py": "REASON = \"vehicle_merged\"\n",
    }) == [
        "app/contact/services/vehicle_party.py",
        "app/inventory/api/stock.py",
        "app/sales/services/offer.py",
    ]
