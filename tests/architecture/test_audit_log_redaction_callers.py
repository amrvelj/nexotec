"""Architecture test (ADR-121, KAN-564; Anto, 2026-11-07): the audit log is
append-only with exactly one sanctioned rewrite.

`app.core.audit.redact_audit_values` blanks named personal keys in an
entity's own rows when a retention purge deletes that entity. Nothing else
may change an audit row:

- `redact_audit_values` is called only from the allow-listed modules below
  (today: the vehicle context's wrapper for a deleted configuration), and
  that wrapper only from the two deletions ADR-121 names — the 30-day purge
  and the discard of a host's failed copy. A new caller is an ADR line plus
  an entry here, never a quiet import.
- No code in `app/` or `scripts/` issues an SQL `UPDATE` or `DELETE` on
  `AuditEvent` / `audit_event`, nor assigns to an audit row's `before`,
  `after`, `action`, `actor_id`, `reason` or `created_at` outside
  `app/core/audit.py`.
"""

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = [_REPO_ROOT / "app", _REPO_ROOT / "scripts"]

# module -> why it may call the function named in the key of _ALLOWED_CALLERS
_ALLOWED_CALLERS: dict[str, set[str]] = {
    # ADR-121: the vehicle context's wrapper (the keys a configuration's rows copy).
    "redact_audit_values": {"app/vehicle/services/configuration.py"},
    # ADR-121: the two deletions of a configuration — the discard of a host's
    # failed copy (KAN-188, in configuration.py) and the 30-day purge (KAN-270).
    "redact_personal_audit_values": {
        "app/vehicle/services/configuration.py",
        "app/vehicle/services/configuration_retention.py",
    },
}

_RAW_WRITE = re.compile(
    r"(update|delete)\(\s*AuditEvent\b|\b(UPDATE|DELETE\s+FROM)\s+audit_event\b", re.IGNORECASE
)


def _python_files():
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path, path.relative_to(_REPO_ROOT).as_posix()


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_only_the_allow_listed_modules_call_the_redaction():
    offenders = []
    for path, rel in _python_files():
        if rel == "app/core/audit.py":
            continue
        called = _called_names(ast.parse(path.read_text(encoding="utf-8")))
        for name, allowed in _ALLOWED_CALLERS.items():
            if name in called and rel not in allowed:
                offenders.append(f"{rel} calls {name}")
    assert offenders == [], offenders


def test_every_allow_listed_caller_still_calls_it():
    """An allow-list entry nobody uses is a door left open."""

    for name, allowed in _ALLOWED_CALLERS.items():
        for rel in allowed:
            called = _called_names(ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8")))
            assert name in called, f"{rel} no longer calls {name}: remove it from the allow-list"


def test_no_raw_update_or_delete_of_audit_rows():
    offenders = [rel for path, rel in _python_files() if _RAW_WRITE.search(path.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_the_guard_sees_a_raw_update():
    """The pattern is not blind (tests.md: prove a guard goes red)."""

    assert _RAW_WRITE.search("db.execute(update(AuditEvent).where(...))")
    assert _RAW_WRITE.search('db.execute(text("UPDATE audit_event SET after = NULL"))')
    assert _RAW_WRITE.search("delete(AuditEvent)")
    assert not _RAW_WRITE.search("record_audit_event(db, ...)")
