"""KAN-775: a pattern ending in `$` is never applied with `.match` or `.search` in `app/`.

Python's `$` also matches just before a final newline, so `re.match(r"^\\d{4}$", "8001\\n")`
is truthy: a value with a trailing newline passed as a postal code, a hex colour, a number
filter or an object key, was stored, and then compared unequal to its clean spelling. The
rule: an anchored pattern is applied with `.fullmatch` (KAN-770 did it for the VIN and the
chassis number; this test holds it for every module of `app/`).

What the walk sees, per module of `app/`:

- a name bound to `re.compile(<literal>)` whose pattern ends in an unescaped `$`;
- the same name imported from the module that compiles it (`from … import HEX_COLOR`);
- a local name bound to an expression that names one (`pattern = _MONEY if … else _NUMBER`);
- `re.match` / `re.search` called with such a literal, or with a module-level string
  constant holding one, directly;
- `re.compile(<literal>).match(…)` chained;
- `module.PATTERN.match(…)` on any module's anchored pattern, reached through `import … as …`.

A literal is a string constant or a `+` concatenation of them. A pattern built from a
formatted string (an f-string, `%`, `.format`) escapes the walk; none exists in `app/` today.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
_PARTIAL = {"match", "search"}


def _ends_in_dollar(pattern: str) -> bool:
    body = pattern.rstrip()
    if not body.endswith("$"):
        return False
    backslashes = len(body[:-1]) - len(body[:-1].rstrip("\\"))
    return backslashes % 2 == 0


def _is_re_call(node: ast.AST, names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in names
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    )


def _const_str(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_str(node.left, constants), _const_str(node.right, constants)
        return left + right if left is not None and right is not None else None
    return None


def _literal(node: ast.Call, constants: dict[str, str] | None = None) -> str | None:
    return _const_str(node.args[0], constants or {}) if node.args else None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound to a string (or a concatenation of strings)."""

    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _const_str(node.value, constants)
            if value is not None:
                constants.update({t.id: value for t in targets if isinstance(t, ast.Name)})
    return constants


def _module_name(path: Path) -> str:
    parts = path.relative_to(APP.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _bound_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _bound_names(element)]
    return []


def _assignments(tree: ast.AST) -> list[tuple[list[str], ast.AST]]:
    found: list[tuple[list[str], ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            found.append(([n for t in node.targets for n in _bound_names(t)], node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            found.append((_bound_names(node.target), node.value))
    return found


def _compiled_anchored(tree: ast.Module) -> set[str]:
    constants = _string_constants(tree)
    names: set[str] = set()
    for targets, value in _assignments(tree):
        if _is_re_call(value, {"compile"}) and (pattern := _literal(value, constants)) is not None and _ends_in_dollar(pattern):
            names.update(targets)
    return names


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _trees() -> dict[str, tuple[Path, ast.Module]]:
    return {_module_name(p): (p, ast.parse(p.read_text(), filename=str(p))) for p in sorted(APP.rglob("*.py"))}


def _anchored_per_module(trees: dict[str, tuple[Path, ast.Module]]) -> dict[str, set[str]]:
    own = {module: _compiled_anchored(tree) for module, (_, tree) in trees.items()}
    anchored: dict[str, set[str]] = {}
    for module, (_, tree) in trees.items():
        names = set(own[module])
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in own:
                names.update(a.asname or a.name for a in node.names if a.name in own[node.module])
        # Aliases: a name bound to an expression that names an anchored pattern is one too.
        changed = True
        while changed:
            changed = False
            for targets, value in _assignments(tree):
                if _names_in(value) & names and not set(targets) <= names:
                    names.update(targets)
                    changed = True
        anchored[module] = names
    return anchored


def _module_aliases(tree: ast.Module, modules: set[str]) -> dict[str, str]:
    """`import app.x.y as m` / `from app.x import y` → {"m": "app.x.y", "y": "app.x.y"}."""

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in modules:
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if f"{node.module}.{a.name}" in modules:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _partial_uses(trees: dict[str, tuple[Path, ast.Module]], anchored: dict[str, set[str]]) -> list[str]:
    offences: list[str] = []
    for module, (path, tree) in trees.items():
        where = path.relative_to(APP.parent)
        constants = _string_constants(tree)
        aliases = _module_aliases(tree, set(trees))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_re_call(node, _PARTIAL) and (pattern := _literal(node, constants)) is not None and _ends_in_dollar(pattern):
                offences.append(f"{where}:{node.lineno} re.{node.func.attr}(r{pattern!r})")  # type: ignore[attr-defined]
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in _PARTIAL):
                continue
            receiver = func.value
            if (
                _is_re_call(receiver, {"compile"})
                and (pattern := _literal(receiver, constants)) is not None  # type: ignore[arg-type]
                and _ends_in_dollar(pattern)
            ):
                offences.append(f"{where}:{node.lineno} re.compile(r{pattern!r}).{func.attr}(…)")
            elif isinstance(receiver, ast.Name) and receiver.id in anchored[module]:
                offences.append(f"{where}:{node.lineno} {receiver.id}.{func.attr}(…)")
            elif isinstance(receiver, ast.Attribute):
                owner = receiver.value
                target = aliases.get(owner.id) if isinstance(owner, ast.Name) else None
                if receiver.attr in (anchored.get(target, set()) if target else anchored[module]):
                    offences.append(f"{where}:{node.lineno} {receiver.attr}.{func.attr}(…)")
    return offences


def test_the_walk_sees_the_anchored_patterns_of_app():
    """Proves the collector works: it finds the patterns KAN-775 names, an imported one
    and an alias among them — a walk that sees nothing would pass the rule vacuously."""

    anchored = _anchored_per_module(_trees())
    assert sum(len(names) for names in anchored.values()) >= 35
    assert {"_POSTAL_CODE_RE", "_E164_RE", "_IBAN_RE"} <= anchored["app.core.validators"]
    assert {"_NUMBER", "_MONEY", "_DAYS", "pattern"} <= anchored["app.core.list_query"]
    assert "HEX_COLOR" in anchored["app.platform.organisation.schemas.branding"]
    assert "_KEY" in anchored["app.core.object_store"]


def test_no_pattern_ending_in_dollar_is_applied_with_match_or_search():
    trees = _trees()
    offences = _partial_uses(trees, _anchored_per_module(trees))
    assert not offences, (
        "A pattern ending in `$` also matches before a final newline under `.match`/`.search` — "
        "apply it with `.fullmatch` (KAN-775):\n" + "\n".join(offences)
    )


def test_the_walk_catches_every_spelling_it_names():
    """Each spelling the docstring names, in a module of its own: each is one offence."""

    source = {
        "app.fake.patterns": 'import re\nDIGITS = r"^\\d" + "{4}$"\nPOSTAL = re.compile(DIGITS)\n',
        "app.fake.user": (
            "import re\nimport app.fake.patterns as p\nfrom app.fake.patterns import POSTAL as P\n"
            'TEXT = r"^x$"\n'
            "def f(v):\n"
            "    a = P.match(v)\n"
            "    b = p.POSTAL.search(v)\n"
            "    c = re.match(TEXT, v)\n"
            '    d = re.compile(r"^y$").match(v)\n'
            "    e = p.POSTAL.fullmatch(v)\n"
            "    return a, b, c, d, e\n"
        ),
    }
    trees = {name: (APP / (name.replace(".", "/")[4:] + ".py"), ast.parse(text)) for name, text in source.items()}
    offences = _partial_uses(trees, _anchored_per_module(trees))
    assert [o.split(" ", 1)[1] for o in offences] == [
        "P.match(…)",
        "POSTAL.search(…)",
        "re.match(r'^x$')",
        "re.compile(r'^y$').match(…)",
    ]


def test_the_dollar_rule_reads_escapes():
    assert _ends_in_dollar(r"^\d{4}$")
    assert not _ends_in_dollar(r"^costs \$")
    assert _ends_in_dollar(r"^a\\$")
    assert not _ends_in_dollar(r"^\d{4}")
