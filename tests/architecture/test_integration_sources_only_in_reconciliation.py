"""Architecture test (KAN-795; ADR-107, ADR-131, ADR-162): a column of
integration's that names a record of a context *above* it is reconciled from
the owner's side — the owner's `reconciliation.py` calls a builder of
`app/integration/services/reconciliation_sources.py`, exported through
`integration.public`, passing its own target model.

Each builder is a ReferenceCheck over integration's tables, scoped to no
Betrieb. Embedded in a live query or called from a service it would read
across out of `test_no_cross_context_orm_query.py`'s and the tenant filter's
sight. So this test confines its users to the integration context itself and
the `reconciliation.py` modules, which only report. A new caller is an ADR plus
an entry here.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"

# Each builder and the context whose reconciliation module calls it.
_BUILDERS = {"message_delivery_recipient_reference_check": "customer"}

_NAMED = re.compile(r"\b(" + "|".join(_BUILDERS) + r")\b")


def _may_use_them(relative: str) -> bool:
    return relative.startswith("app/integration/") or relative.endswith("/reconciliation.py")


def _users_outside_reconciliation(sources: dict[str, str]) -> list[str]:
    """The `app/…` paths, of `sources` (path -> text), that name a builder but may not."""

    return [path for path, text in sorted(sources.items()) if _NAMED.search(text) and not _may_use_them(path)]


def test_the_integration_sources_are_used_only_by_reconciliation():
    offenders = _users_outside_reconciliation(
        {str(path.relative_to(_REPO_ROOT)): path.read_text() for path in sorted(_APP.rglob("*.py"))}
    )

    assert offenders == [], (
        "integration.public's reconciliation sources are for reconciliation only (KAN-795); "
        f"a new caller needs an ADR and an entry in this test: {offenders}"
    )


def test_every_builder_is_still_called_by_its_owner_reconciliation():
    # Not vacuous: the owner still runs each check.
    missing = [
        name
        for name, owner in _BUILDERS.items()
        if f"{name}(" not in (_APP / owner / "reconciliation.py").read_text()
    ]
    assert missing == []


def test_the_guard_reports_a_user_outside_reconciliation():
    # Red before green on synthetic sources.
    assert _users_outside_reconciliation({
        "app/customer/services/customer.py": "from app.integration.public import message_delivery_recipient_reference_check\n",
        "app/customer/reconciliation.py": "message_delivery_recipient_reference_check(target_model=C, target_id_column=C.id)\n",
        "app/integration/services/reconciliation_sources.py": "def message_delivery_recipient_reference_check(): ...\n",
        "app/sales/services/untouched.py": "x = 1\n",
        # A longer name is not a use.
        "app/sales/services/deal.py": "message_delivery_recipient_reference_check_cached = 1\n",
    }) == ["app/customer/services/customer.py"]
