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

KAN-737 added the two spellings with the group id on the other side: a
column compared TO a group id (`X.tenant_id == scope.group_id`), and a group
id handed to a call as its `tenant_id=` (an AST check, since a call spans
lines; the group-owned writes `record_audit_event` and `OutboxEvent` aside).

An exact file allowlist, not a directory-level one: a directory exemption
would let a second, unrelated function anywhere in one of these packages
slip through unnoticed. Each entry's reason is inline below, and the
group stock roster's (since KAN-121 in platform/organisation/services/group_read_dealerships.py,
before it in inventory/services/group_listing.py) is the one to read closely — it did NOT get folded
into app.core.tenancy.get_group_read_or_404 as the ticket's own preferred
option asked for, and that decision is explained where it's made.
"""

import ast
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
    # KAN-617 split customer.py along its seams; the same reads, moved —
    # not new ones (the KAN-121 precedent below). Each file is listed by
    # name, so a new customer module with a group predicate still fails.
    _APP_ROOT / "customer" / "services" / "customer_common.py",
    _APP_ROOT / "customer" / "services" / "customer_merge.py",
    _APP_ROOT / "customer" / "services" / "customer_search.py",
    _APP_ROOT / "customer" / "services" / "trade_in_holders.py",
    _APP_ROOT / "customer" / "services" / "vehicle_party.py",
    _APP_ROOT / "customer" / "services" / "vehicle_party_locks.py",
    _APP_ROOT / "customer" / "services" / "vehicle_party_reads.py",
    # KAN-524's vehicle merge, found by KAN-737's right-hand pattern: after the
    # merge moves the duplicate's party rows onto the survivor, each role is
    # settled per customer group — `groups.get(party.customer_id) == group_id`
    # partitions rows ALREADY loaded for the one merged vehicle, in Python, by
    # each party's own customer's group. No query, no wider read.
    _APP_ROOT / "customer" / "services" / "vehicle_merge.py",
    # The ADR-030 compliance layer built on top of tenancy.py's helper.
    _APP_ROOT / "customer" / "services" / "legal_basis.py",
    # KAN-218 (FR-A-44, ADR-014, ADR-166): the export of ONE Betrieb's
    # customers — the group in the predicate is that Betrieb's own, handed in
    # by platform (`ExportScope.group_id`), never another's, narrowed further
    # to the Betrieb's relationship. Never a dealer-facing read: the worker's
    # export only.
    _APP_ROOT / "customer" / "services" / "data_export.py",
    # KAN-28 — ADR-055's group-readable stock roster. Since KAN-121 (rule
    # 2) its group predicate is platform's: the Betriebe of the caller's
    # own group while the group's read is enabled, as plain data
    # (`platform.public.list_group_read_dealerships`); Stock selects its own
    # rows by those ids and app/inventory/services/group_listing.py no
    # longer filters on dealer_group_id, so it left this list. The same
    # read, moved — not a new one. Not folded into get_group_read_or_404
    # (one row by id; app/core/tenancy.py is preserved) for KAN-28's
    # reason: this is a roster, not one entity. Authorise before the
    # lookup, 404 never 403: Stock still checks the role and the caller's
    # own group first, then turns platform's None (read off, or no such
    # group) into its 404. Who may call it is confined by
    # test_group_read_dealerships_callers.py (Stock's group listing only).
    _APP_ROOT / "platform" / "organisation" / "services" / "group_read_dealerships.py",
    # Platform's OWN cross-group dealership listing — platform_admin
    # managing the entire multi-tenant system (require_access_role(),
    # platform_admin-only, at app/platform/organisation/api/dealerships.py's
    # list_dealerships route). Not the threat model this rule polices: no
    # dealer or sister group can reach this, only the SaaS operator's own
    # staff — the same already-accepted exception category as
    # ADR-014/030's "one narrow exception" note in CLAUDE.md, one level
    # up (a superuser role bypassing tenant scope entirely, not an
    # ambient relaxation of it for an ordinary dealer-facing read).
    _APP_ROOT / "platform" / "organisation" / "services" / "dealership.py",
    # KAN-371, ADR-093: the History tab's names for a group-owned customer —
    # a Betrieb's and a user's name, never more. The caller's own Betrieb
    # always; a sister Betrieb and its users only while the group's read is
    # enabled (ADR-030's joint-controller flag). A dealer-facing read, so it
    # sits in its own module rather than beside the staff-only listing above.
    _APP_ROOT / "platform" / "identity" / "services" / "group_labels.py",
    # KAN-722 (Anto, 2026-11-15): which Betrieb of the CALLER's group wrote a
    # group customer's audit row — the group's Betriebe (the offboarded ones,
    # or the one being exported) and the users whose only Betrieb in that
    # group is one of them. Ids only, never a row; the group is the
    # caller's own (the History tab: the token's group; the export: the
    # Betrieb's own group from platform's scope). Used to hide an offboarded
    # Betrieb's entries from its sisters and to put exactly those in its
    # archive. Its own module, like group_labels.py above.
    _APP_ROOT / "platform" / "identity" / "services" / "audit_attribution.py",
    # KAN-722: the core exporter's half of that rule — a Betrieb's export (and
    # its last-write and snapshot checks) takes the audit rows of a group
    # customer's History it wrote: `tenant_id` = the Betrieb's OWN group,
    # handed in by platform (`ExportScope.group_id`), never another's, and
    # narrowed to the rows attributed to that Betrieb. The export job's read
    # only, never a dealer-facing read — the customer exporter's precedent
    # (customer/services/data_export.py above).
    _APP_ROOT / "core" / "export_core.py",
    # KAN-218, found by KAN-737's right-hand pattern: platform's half of the
    # Betrieb export takes the ONE dealer_group row that is the Betrieb's own
    # group (`group.c.id == scope.group_id`, the group handed in by
    # platform's own ExportScope), never another's. The export job's read only,
    # never a dealer-facing read — the exporters' precedent above.
    _APP_ROOT / "platform" / "services" / "data_export_tables.py",
    # KAN-467, ADR-039 as amended (Anto, 2026-10-31: "it's on a group
    # level"): the Kontrollschild register is the dealer group's own, like
    # the customer register above — every read and write filters by the
    # CALLER's group_id (from the token), never a wider one. Not a boundary crossing: the data's rightful scope.
    _APP_ROOT / "vehicle" / "services" / "plate.py",
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
    # KAN-737: the group id on the RIGHT of the comparison —
    # `Audit.tenant_id == principal.group_id`, `Group.id == scope.group_id`,
    # `Row.owner_id == group_id`. Another column compared to a group id is a
    # group-scoped predicate just the same; the left-hand pattern above never
    # saw it. The optional prefix is the operand's own dotted name ending in a
    # dot, so `plate_group_id` (no dot before `group_id`) stays out, as above.
    "group id on the right (== …group_id)": re.compile(rf"==\s*(?:[A-Za-z_][\w.]*\.)?{_GROUP_COLUMN}\b"),
}

# KAN-737: a group id handed to a helper AS its tenant — `list_audit_events(…,
# tenant_id=principal.group_id)` — scopes that helper's read to the whole group
# without one `==` in sight. A call spans lines, so this is an AST check, not a
# line pattern. The callees below are WRITES that file a group-owned record's
# row under its group (ADR-014: a group customer's audit row and outbox event
# carry the group as tenant), never a read; any other callee — a new read helper
# above all — is a finding unless its file is allow-listed above.
_GROUP_TENANT_WRITERS = frozenset({"record_audit_event", "OutboxEvent"})
_GROUP_TENANT_KWARG = "tenant_id=<group id> in a call"


def _is_group_id(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"group_id", "dealer_group_id"}
    return isinstance(node, ast.Attribute) and node.attr in {"group_id", "dealer_group_id"}


def _callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _group_tenant_kwargs(source: str) -> list[tuple[int, str]]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _callee_name(node.func) in _GROUP_TENANT_WRITERS:
            continue
        for keyword in node.keywords:
            if keyword.arg == "tenant_id" and _is_group_id(keyword.value):
                call = f"{_callee_name(node.func)}(tenant_id={ast.unparse(keyword.value)})"
                hits.append((keyword.value.lineno, call))
    return hits


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
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if path in _ALLOWED_FILES:
            continue
        for lineno, call in _group_tenant_kwargs(path.read_text(encoding="utf-8")):
            all_violations.append(f"[{_GROUP_TENANT_KWARG}] {path.relative_to(_REPO_ROOT)}:{lineno}: {call}")

    assert not all_violations, (
        "Found a group-scoped filter predicate (group_id or dealer_group_id) outside "
        "the sanctioned files (app/core/tenancy.py, app/customer/services/customer.py, "
        "app/customer/services/legal_basis.py, app/platform/organisation/services/group_read_dealerships.py, "
        "app/platform/organisation/services/dealership.py, …; see _ALLOWED_FILES) — any other cross-group-boundary read "
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
    "group id on the right (== …group_id)": [
        # KAN-737's two real shapes: export_core's audit predicate and
        # data_export_tables' group row, plus a bare name.
        "_audit.c.tenant_id == scope.group_id,",
        "stmt = stmt.where(AuditEvent.tenant_id == principal.group_id)",
        "stmt = stmt.where(Row.owner_id == dealer_group_id)",
    ],
}

_SAFE_WRITES_BY_SPELLING = {
    "equality (==)": ['customer = Customer(group_id=group_id, first_name="Anna")'],
    "membership (.in_())": ['stmt = stmt.where(Customer.id.in_(ids))'],
    "filter_by(group_id=…)": ['customer = Customer(group_id=group_id, first_name="Anna")'],
    "raw SQL (text(…))": ['db.execute(text("SELECT 1"))'],
    "group id on the right (== …group_id)": [
        "stmt = stmt.where(AuditEvent.tenant_id == principal.tenant_id)",
        "same = other.plate_group_id == this.plate_group_id",
    ],
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


def test_a_group_id_handed_to_a_read_as_its_tenant_is_caught():
    """KAN-737: customers.py before KAN-722 called
    `list_audit_events(…, tenant_id=principal.group_id)` — over several
    lines, no `==` anywhere. The AST check must see it; the group-owned
    WRITES (an audit row, an outbox event) must stay unflagged."""

    read = "rows = list_audit_events(\n    db,\n    entity_id=customer.id,\n    tenant_id=principal.group_id,\n)\n"
    assert _group_tenant_kwargs(read) == [(4, "list_audit_events(tenant_id=principal.group_id)")]
    bare = "rows = audit.list_events(db, tenant_id=group_id)\n"
    assert _group_tenant_kwargs(bare) == [(1, "list_events(tenant_id=group_id)")]
    writes = (
        "record_audit_event(db, tenant_id=customer.group_id, action='x')\n"
        "db.add(OutboxEvent(tenant_id=group_id, event_type='customer.created'))\n"
        "list_audit_events(db, tenant_id=principal.tenant_id)\n"
    )
    assert _group_tenant_kwargs(writes) == []
