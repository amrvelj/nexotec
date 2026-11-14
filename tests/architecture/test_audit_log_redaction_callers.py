"""Architecture test (ADR-121, KAN-564; Anto, 2026-11-07): the audit log is
append-only with exactly one sanctioned rewrite.

`app.core.audit.redact_audit_values` blanks named personal keys in an
entity's own rows when that entity is deleted for retention (or as a
compensation removing what no host kept). What this test guards, over every
module in `app/` and `scripts/` but `app/core/audit.py`:

- `redact_audit_values` and the vehicle context's wrapper
  `redact_personal_audit_values` are imported (under any alias) and called
  only from the allow-listed modules below; every listed module still calls
  them. A new caller is an ADR line plus an entry here.
- No SQL or ORM statement updates or deletes audit rows: `update(AuditEvent)`,
  `delete(AuditEvent)`, an `.update()`/`.delete()` called on a chain built from
  `query(AuditEvent)` (on one line or several),
  `AuditEvent.__table__.update()/.delete()`, or `UPDATE`/`DELETE FROM` on
  `audit_event`, quoted or not.
- Nothing assigns to an object's `before` or `after` attribute (`x.after = …`,
  `setattr(x, "after", …)`) or marks one modified (`flag_modified(x, "after")`,
  the step an in-place change of the payload needs) — the ways an audit row's
  payload is changed.

Not guarded, because they cannot be told apart statically from legitimate
code: `db.delete(row)` where `row` happens to be an audit row, and a query
stored in a variable before `.delete()`. Review is the guard there.
"""

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = [_REPO_ROOT / "app", _REPO_ROOT / "scripts"]

# module -> why it may call the function named in the key of _ALLOWED_CALLERS
_ALLOWED_CALLERS: dict[str, set[str]] = {
    # ADR-121: the vehicle context's wrapper (the keys a configuration's rows copy).
    # KAN-218 (Q3 A; ADR-121 addendum (KAN-218, proposed; Anto's answer on the
    # ten-year deletion pending)): the deletion of an offboarded Betrieb after its
    # ten years blanks the values of every entity of its audit log; the rows stay.
    "redact_audit_values": {"app/vehicle/services/configuration.py", "app/core/export_core.py"},
    # ADR-121: the two deletions of a configuration — the discard of a host's
    # failed copy (KAN-188, in configuration.py) and the 30-day purge (KAN-270).
    "redact_personal_audit_values": {
        "app/vehicle/services/configuration.py",
        "app/vehicle/services/configuration_retention.py",
    },
}

_RAW_WRITE = re.compile(
    r"(update|delete)\(\s*AuditEvent\b"
    r"|query\(\s*AuditEvent\s*\)[^\n]*\.(update|delete)\("
    r"|AuditEvent\.__table__\.(update|delete)\("
    r"|\b(UPDATE|DELETE\s+FROM)\s+[\"`]?audit_event\b",
    re.IGNORECASE,
)
_PAYLOAD_ATTRIBUTES = {"before", "after"}


def _python_files():
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path, path.relative_to(_REPO_ROOT).as_posix()


def _called_names(tree: ast.AST) -> set[str]:
    """Names called, and names imported (under any alias, `as` resolved to
    the imported name) — an aliased import is a use of the original."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _chain_has_audit_query(node: ast.AST) -> bool:
    """Whether a call chain (`a(...).b(...).c`) was built from `query(AuditEvent)`."""

    while True:
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name == "query" and any(isinstance(arg, ast.Name) and arg.id == "AuditEvent" for arg in node.args):
                return True
            node = func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return False


def _orm_writes(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"update", "delete"}
        and _chain_has_audit_query(node.func.value)
    ]


def _payload_writes(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "flag_modified"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _PAYLOAD_ATTRIBUTES
        ):
            lines.append(node.lineno)
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in _PAYLOAD_ATTRIBUTES:
                lines.append(node.lineno)
    return lines


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
    offenders = [
        rel
        for path, rel in _python_files()
        if rel != "app/core/audit.py" and _RAW_WRITE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders


def test_no_orm_update_or_delete_on_an_audit_query():
    offenders = []
    for path, rel in _python_files():
        if rel == "app/core/audit.py":
            continue
        for line in _orm_writes(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{rel}:{line}")
    assert offenders == [], offenders


def test_nothing_assigns_to_a_before_or_after_attribute():
    offenders = []
    for path, rel in _python_files():
        if rel == "app/core/audit.py":
            continue
        for line in _payload_writes(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{rel}:{line}")
    assert offenders == [], offenders


def test_the_guard_sees_a_raw_update():
    """The pattern is not blind (tests.md: prove a guard goes red)."""

    assert _RAW_WRITE.search("db.execute(update(AuditEvent).where(...))")
    assert _RAW_WRITE.search('db.execute(text("UPDATE audit_event SET after = NULL"))')
    assert _RAW_WRITE.search("delete(AuditEvent)")
    assert _RAW_WRITE.search("db.query(AuditEvent).filter(x).update({...})")
    assert _RAW_WRITE.search("db.query(AuditEvent).delete()")
    assert _RAW_WRITE.search("db.execute(AuditEvent.__table__.update().values(after=None))")
    assert _RAW_WRITE.search('db.execute(text(\'DELETE FROM "audit_event"\'))')
    assert not _RAW_WRITE.search("record_audit_event(db, ...)")


def test_the_guard_sees_an_aliased_import_and_a_payload_write():
    tree = ast.parse(
        "from app.core.audit import redact_audit_values as scrub\n"
        "row.after = {}\n"
        "event.before += {}\n"
        "setattr(row, 'after', {})\n"
        "flag_modified(row, 'before')\n"
        "(\n    db.query(AuditEvent)\n    .filter(x)\n    .delete()\n)\n"
    )
    assert "redact_audit_values" in _called_names(tree)
    assert _payload_writes(tree) == [2, 3, 4, 5]
    assert len(_orm_writes(tree)) == 1
    assert _orm_writes(ast.parse("db.query(Customer).filter(x).delete()")) == []
