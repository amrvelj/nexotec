"""Architecture test (WP-3 PR-4, ADR-014/030): import-linter enforces
cross-CONTEXT boundaries, but it's blind to what a query filters on — it
cannot tell "Customer.group_id == principal.group_id" (this context's own
rightful scope, established by PR-2) from "Customer.group_id ==
<some other group>" (an ambient relaxation of the group boundary, which
ADR-014/030 say must go through exactly one gated function). Same gap
CLAUDE.md already documents for ADR-047's cross-context-write rule: it
needs its own test, not an import-linter contract.

This proves a `group_id ==` filter predicate appears ONLY in the three
files that are allowed to write one:
  - app/core/tenancy.py — get_group_read_or_404, THE one enumerated
    cross-boundary read function (generic; app.core cannot import any
    bounded context, so the actual compliance predicate is supplied by
    the caller).
  - app/customer/services/customer.py — Customer's own native, unconditional
    group scope (PR-2): filtering by the CALLER's own group_id is not a
    boundary crossing, it's the data's rightful scope.
  - app/customer/services/legal_basis.py — the ADR-030 compliance layer
    built on top of tenancy.py's helper.

An exact file allowlist, not a directory-level one: a directory exemption
would let a second, unrelated function anywhere in app/customer/services/
slip through unnoticed.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_ALLOWED_FILES = {
    _APP_ROOT / "core" / "tenancy.py",
    _APP_ROOT / "customer" / "services" / "customer.py",
    _APP_ROOT / "customer" / "services" / "legal_basis.py",
}

# Whitespace-tolerant so `group_id==x`, `group_id == x` are both caught.
# Requires the second `=` so a constructor kwarg (`group_id=value`, a
# WRITE, not a read filter) is never flagged.
_PATTERN = re.compile(r"\bgroup_id\s*==")


def test_no_ambient_group_id_filter_outside_the_sanctioned_files():
    violations = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if path in _ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found a `group_id ==` filter predicate outside the three files allowed to "
        "have one (app/core/tenancy.py, app/customer/services/customer.py, "
        "app/customer/services/legal_basis.py) — any other cross-group-boundary "
        "read must go through app.core.tenancy.get_group_read_or_404, never an "
        "ambient filter written inline elsewhere:\n" + "\n".join(violations)
    )


def test_the_sanctioned_files_still_exist():
    """A trivially-passing test above (an empty violations list) is
    indistinguishable from the three files having been renamed out from
    under the allowlist — this catches that.
    """

    missing = [str(p.relative_to(_REPO_ROOT)) for p in _ALLOWED_FILES if not p.exists()]
    assert not missing, f"Allowlisted file(s) no longer exist, update this test: {missing}"
