"""Architecture test (WP-3 PR-4, ADR-014/030): import-linter enforces
cross-CONTEXT boundaries, but it's blind to what a query filters on — it
cannot tell "Customer.group_id == principal.group_id" (this context's own
rightful scope, established by PR-2) from "Customer.group_id ==
<some other group>" (an ambient relaxation of the group boundary, which
ADR-014/030 say must go through exactly one gated function). Same gap
CLAUDE.md already documents for ADR-047's cross-context-write rule: it
needs its own test, not an import-linter contract.

This proves a group-scoped filter predicate — on `group_id` OR
`dealer_group_id`, in ANY of the spellings a caller could actually write
one (`==`, `.in_(`, `filter_by(...)`, or a raw-SQL string) — appears ONLY
in the explicitly enumerated files below. KAN-28 widened this from a
single `\\bgroup_id\\s*==` regex, which missed `filter_by(group_id=...)`,
`.in_()`, join conditions (structurally the same `==` under SQLAlchemy,
so PATTERN 1 already covers them — no separate pattern needed), raw SQL,
and `dealer_group_id` entirely (a genuinely different column that
`app/inventory/services/group_listing.py` filtered on — exempt only by
accident of spelling, not by decision).

An exact file allowlist, not a directory-level one: a directory exemption
would let a second, unrelated function anywhere in one of these packages
slip through unnoticed. Each entry's reason is inline below, and
group_listing.py's is the one to read closely — it did NOT get folded
into app.core.tenancy.get_group_read_or_404 as the ticket's own preferred
option asked for, and that decision is explained where it's made.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_ALLOWED_FILES = {
    # THE one enumerated cross-boundary read function (generic; app.core
    # cannot import any bounded context, so the actual compliance
    # predicate is supplied by the caller). Single-row shape:
    # model.id == entity_id AND model.group_id == group_id.
    _APP_ROOT / "core" / "tenancy.py",
    # Customer's own native, unconditional group scope (PR-2): filtering
    # by the CALLER's own group_id is not a boundary crossing, it's the
    # data's rightful scope.
    _APP_ROOT / "customer" / "services" / "customer.py",
    # The ADR-030 compliance layer built on top of tenancy.py's helper.
    _APP_ROOT / "customer" / "services" / "legal_basis.py",
    # KAN-28 (this ticket) — ADR-055's group-readable stock roster.
    # NOT folded into get_group_read_or_404, deliberately: that helper's
    # shape is "one row by (model, entity_id, group_id)" — this is a
    # roster with no single entity_id, filtered through a JOIN
    # (Dealership.dealer_group_id, since StockItem has no group_id column
    # at all — Stock is dealership-scoped, unlike Customer). Forcing this
    # into the single-row helper's signature would mean either misusing
    # it or changing it, and app/core/tenancy.py is on CLAUDE.md's
    # preserve-verbatim list. What actually matters — authorise BEFORE
    # the row lookup, 404 never 403 — this module already does natively
    # (is_authorized() checked first, then group ownership, both raising
    # NotFoundError); see the module's own docstring for the full
    # reasoning. A decision, not an accident of column naming.
    _APP_ROOT / "inventory" / "services" / "group_listing.py",
    # Platform's OWN cross-group dealership listing — platform_admin
    # managing the entire multi-tenant system (require_access_role(),
    # platform_admin-only, at app/platform/api/dealerships.py's
    # list_dealerships route). Not the threat model this rule polices: no
    # dealer or sister group can reach this, only the SaaS operator's own
    # staff — the same already-accepted exception category as
    # ADR-014/030's "one narrow exception" note in CLAUDE.md, one level
    # up (a superuser role bypassing tenant scope entirely, not an
    # ambient relaxation of it for an ordinary dealer-facing read).
    _APP_ROOT / "platform" / "services" / "dealership.py",
}

# Whole-word only — \b keeps this from matching plate_group_id (a
# genuinely different column) or the "group_id" tail inside
# "dealer_group_id" a second time.
_GROUP_COLUMN = r"(?:group_id|dealer_group_id)"

_PATTERNS: dict[str, re.Pattern[str]] = {
    # `Model.group_id == x` — equality comparison. Requires the second
    # `=` so a constructor kwarg (`group_id=value`, a WRITE) is never
    # flagged. A SQLAlchemy join condition (`.join(Model, Model.group_id
    # == Other.x)`) is the same `==` syntax, so it's caught here too —
    # deliberately no separate "join condition" pattern.
    "equality (==)": re.compile(rf"\b{_GROUP_COLUMN}\s*=="),
    # `Model.group_id.in_(...)` — membership filter.
    "membership (.in_())": re.compile(rf"\b{_GROUP_COLUMN}\s*\.in_\("),
    # `.filter_by(group_id=x)` — the single-equals KWARG form of a read
    # filter. Anchored to a `filter_by(...)` call specifically (not a
    # bare `group_id=value`) so a constructor/dict kwarg elsewhere on the
    # same line is never flagged — same principle the `==` pattern's own
    # "second =" requirement already applies.
    "filter_by(group_id=…)": re.compile(rf"\bfilter_by\([^)]*\b{_GROUP_COLUMN}\s*=(?!=)"),
    # Raw SQL mentioning the column inside a text(...) call — e.g.
    # text("... WHERE group_id = :gid ..."). Anchored to text(...) for
    # the same reason filter_by(...) is anchored above: a bare quoted
    # `group_id = "..."` elsewhere is not raw SQL. Line-based, like every
    # pattern here — a text(...) call split across multiple lines is not
    # caught; none exist in this codebase today (see the module
    # docstring's own check before adding this pattern).
    "raw SQL (text(…))": re.compile(rf"\btext\([^)]*\b{_GROUP_COLUMN}\s*="),
}


def _find_violations(pattern: re.Pattern[str]) -> list[str]:
    violations = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if path in _ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    return violations


def test_no_ambient_group_filter_outside_the_sanctioned_files():
    all_violations: list[str] = []
    for label, pattern in _PATTERNS.items():
        for violation in _find_violations(pattern):
            all_violations.append(f"[{label}] {violation}")

    assert not all_violations, (
        "Found a group-scoped filter predicate (group_id or dealer_group_id) outside "
        "the sanctioned files (app/core/tenancy.py, app/customer/services/customer.py, "
        "app/customer/services/legal_basis.py, app/inventory/services/group_listing.py, "
        "app/platform/services/dealership.py) — any other cross-group-boundary read "
        "must go through app.core.tenancy.get_group_read_or_404, never an ambient "
        "filter written inline elsewhere:\n" + "\n".join(all_violations)
    )


def test_the_sanctioned_files_still_exist():
    """A trivially-passing test above (an empty violations list) is
    indistinguishable from an allowlisted file having been renamed out
    from under it — this catches that.
    """

    missing = [str(p.relative_to(_REPO_ROOT)) for p in _ALLOWED_FILES if not p.exists()]
    assert not missing, f"Allowlisted file(s) no longer exist, update this test: {missing}"


# --- anti-vacuous: the rule must actually be able to fail --------------------------
#
# A rule that has never been observed to fail is not evidence it works — it could
# just as easily never fire. Each spelling gets a deliberately-introduced violation
# it MUST catch, and a same-shaped WRITE that it MUST NOT — proving both that a
# real leak of that spelling is caught, and that the pattern isn't so broad it
# flags legitimate code (which would make the allowlist balloon and the rule
# worthless).

_VIOLATIONS_BY_SPELLING = {
    "equality (==)": [
        'stmt = stmt.where(Customer.group_id == other_group_id)',
        'stmt = stmt.where(Dealership.dealer_group_id == requested_group_id)',
        # A join condition — the same == syntax, proving no separate
        # "join condition" pattern was needed.
        'query.join(Dealership, Dealership.dealer_group_id == DealerGroup.id)',
    ],
    "membership (.in_())": [
        'stmt = stmt.where(Customer.group_id.in_(other_group_ids))',
        'stmt = stmt.where(Dealership.dealer_group_id.in_(other_group_ids))',
    ],
    "filter_by(group_id=…)": [
        'rows = db.query(Customer).filter_by(group_id=other_group_id).all()',
        'rows = db.query(Dealership).filter_by(dealer_group_id=other_group_id).all()',
    ],
    "raw SQL (text(…))": [
        'db.execute(text("SELECT * FROM customer WHERE group_id = :gid"), {"gid": other_group_id})',
    ],
}

_SAFE_WRITES_BY_SPELLING = {
    "equality (==)": ['customer = Customer(group_id=group_id, first_name="Anna")'],
    "membership (.in_())": ['stmt = stmt.where(Customer.id.in_(ids))'],
    "filter_by(group_id=…)": ['customer = Customer(group_id=group_id, first_name="Anna")'],
    "raw SQL (text(…))": ['db.execute(text("SELECT 1"))'],
}


def test_each_spelling_catches_a_deliberate_violation():
    for label, lines in _VIOLATIONS_BY_SPELLING.items():
        pattern = _PATTERNS[label]
        for line in lines:
            assert pattern.search(line), f"[{label}] pattern did not catch: {line!r}"


def test_each_spelling_leaves_the_matching_write_alone():
    for label, lines in _SAFE_WRITES_BY_SPELLING.items():
        pattern = _PATTERNS[label]
        for line in lines:
            assert not pattern.search(line), f"[{label}] pattern falsely flagged a write: {line!r}"


def test_plate_group_id_is_never_confused_with_group_id():
    """A real column (vehicle_plate.plate_group_id, ADR-014-unrelated) that
    contains "group_id" as a substring — proves \\b keeps every pattern
    from matching it.
    """

    line = "shares_group = plate_group_id is not None and other.plate_group_id == plate_group_id"
    for label, pattern in _PATTERNS.items():
        assert not pattern.search(line), f"[{label}] falsely matched plate_group_id: {line!r}"
