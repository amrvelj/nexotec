"""The refusal reasons `app/` raises as a value, not a literal (KAN-801).

KAN-679 taught the twin scans to read `"reason": "literal"`, KAN-796 a
module-level constant (`"reason": RECIPIENT_REFUSED`). What is left is a reason
that arrives as an expression: a helper's parameter (`_refuse(reason, message)`,
`"reason": reason`), a local picked by a branch, an enum's `.value`, a
dataclass's `.code`. This module finds every such site and resolves what it
can by reading the code:

- a **parameter** — its default, and the argument of every call to the
  function (positional or keyword), each resolved again where it is called
  (a pass-through such as `reason=reason` follows the chain up);
- a **local** — every value assigned to it in the function, tuple unpacking
  included;
- a **literal** or a **module-level string constant** — as the older scans do.

A site it cannot resolve (an attribute, an f-string, a call, `**kwargs`) is
not guessed: `refusal_reason_value_sites.json` lists it with its value set
written by hand and where the values come from, or as not a refusal (an
audit row, an outbox payload, a log line, a sort column) with the reason.
The JSON lists the resolved sites too, with their values, so the vitest twin
(`apiRefusal.test.ts`) reads the whole set without an AST of its own; the
backend-lane test keeps every entry equal to what this module reads.

A site is keyed `<path>::<function>::<expression>` — no line numbers, so an
edit elsewhere in the file does not move it.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SITES_JSON = Path(__file__).resolve().parent / "refusal_reason_value_sites.json"


class Unresolved(Exception):
    """The expression's values cannot be read from the code."""


@dataclasses.dataclass(frozen=True)
class Site:
    path: str  # relative to the repository root, forward slashes
    function: str  # the enclosing function's qualified name, or `<module>`
    expression: str  # ast.unparse of the value under "reason"

    @property
    def key(self) -> str:
        return f"{self.path}::{self.function}::{self.expression}"


@dataclasses.dataclass
class _Function:
    module: str
    qualname: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    parent: _Function | None


def _is_constant_name(name: str) -> bool:
    return name.isupper() or (name[:1].isupper() and name.replace("_", "").isupper())


class _Index:
    def __init__(self, app_root: Path) -> None:
        self.root = app_root.parent
        self.trees: dict[str, ast.Module] = {}
        for path in sorted(app_root.rglob("*.py")):
            rel = path.relative_to(self.root).as_posix()
            self.trees[rel] = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        self.constants: dict[str, dict[str, str]] = {}  # module -> NAME -> value
        self.functions: dict[str, list[_Function]] = {}  # bare name -> definitions
        self.enclosing: dict[int, _Function] = {}  # id(node) -> innermost function
        self.calls: dict[str, list[tuple[ast.Call, str, _Function | None]]] = {}
        for module, tree in self.trees.items():
            own: dict[str, str] = {}
            for stmt in tree.body:
                target = None
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                elif isinstance(stmt, ast.AnnAssign):
                    target = stmt.target
                value = getattr(stmt, "value", None)
                if (
                    isinstance(target, ast.Name)
                    and _is_constant_name(target.id)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    own[target.id] = value.value
            self.constants[module] = own
            self._walk(module, tree, None, "")

    def _walk(self, module: str, node: ast.AST, function: _Function | None, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                entry = _Function(module, qualname, child, function)
                self.functions.setdefault(child.name, []).append(entry)
                self.enclosing[id(child)] = entry
                self._walk(module, child, entry, f"{qualname}.")
                continue
            if isinstance(child, ast.ClassDef):
                self._walk(module, child, function, f"{prefix}{child.name}.")
                continue
            if function is not None:
                self.enclosing[id(child)] = function
            if isinstance(child, ast.Call):
                name = _called_name(child.func)
                if name is not None:
                    self.calls.setdefault(name, []).append((child, module, function))
            self._walk(module, child, function, prefix)

    def constant(self, module: str, name: str) -> set[str]:
        if name in self.constants[module]:
            return {self.constants[module][name]}
        return {values[name] for values in self.constants.values() if name in values}


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [*node.args.posonlyargs, *node.args.args]


def _default_of(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> ast.expr | None:
    positional = _parameters(node)
    defaults = node.args.defaults
    for arg, default in zip(positional[len(positional) - len(defaults) :], defaults, strict=True):
        if arg.arg == name:
            return default
    for arg, kw_default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if arg.arg == name:
            return kw_default
    return None


def _is_parameter(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(arg.arg == name for arg in (*_parameters(node), *node.args.kwonlyargs))


def _argument_for(call: ast.Call, function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> ast.expr | None:
    """The expression a call passes for parameter `name`, or None when the
    call leaves it to the default. Raises Unresolved on *args / **kwargs."""

    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
        if keyword.arg is None:
            raise Unresolved(f"a call passes **{ast.unparse(keyword.value)}")
    positional = [arg.arg for arg in _parameters(function)]
    if positional and positional[0] in ("self", "cls") and isinstance(call.func, ast.Attribute):
        positional = positional[1:]
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        raise Unresolved("a call passes *args")
    if name in positional and positional.index(name) < len(call.args):
        return call.args[positional.index(name)]
    return None


def _assigned_values(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> list[ast.expr]:
    values: list[ast.expr] = []
    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    values.append(stmt.value)
                elif isinstance(target, ast.Tuple) and isinstance(stmt.value, ast.Tuple):
                    for element, value in zip(target.elts, stmt.value.elts, strict=False):
                        if isinstance(element, ast.Name) and element.id == name:
                            values.append(value)
                elif isinstance(target, ast.Tuple) and any(
                    isinstance(e, ast.Name) and e.id == name for e in target.elts
                ):
                    raise Unresolved(f"{name} is unpacked from {ast.unparse(stmt.value)}")
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == name and stmt.value is not None:
                if isinstance(stmt, ast.AugAssign):
                    raise Unresolved(f"{name} is built with {ast.unparse(stmt)}")
                values.append(stmt.value)
        elif isinstance(stmt, (ast.For, ast.comprehension)) and isinstance(stmt.target, ast.AST):
            if any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(stmt.target)):
                raise Unresolved(f"{name} is a loop variable")
        elif isinstance(stmt, ast.ExceptHandler) and stmt.name == name:
            raise Unresolved(f"{name} is an exception")
    return values


class Resolver:
    def __init__(self, app_root: Path = APP) -> None:
        self.index = _Index(app_root)
        self._hints: dict[str, dict] = {}

    def sites(self) -> list[Site]:
        """Every `"reason": <expression>` in a dict display whose value is
        neither a string literal nor a module-level constant's name."""

        found: list[Site] = []
        for module, tree in self.index.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values, strict=True):
                    if not (isinstance(key, ast.Constant) and key.value == "reason"):
                        continue
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        continue
                    if isinstance(value, ast.Name) and _is_constant_name(value.id):
                        continue
                    function = self.index.enclosing.get(id(node))
                    found.append(Site(module, function.qualname if function else "<module>", ast.unparse(value)))
        return sorted(set(found), key=lambda site: site.key)

    def values(self, site: Site, hints: dict[str, dict] | None = None) -> set[str]:
        """The values the site can carry, read from the code; Unresolved when
        any one of them cannot be read.

        `hints` (from the JSON entry) say how to read an expression the scan
        cannot follow on its own, keyed by its source text:
        `{"argumentsOf": "<callable>", "parameter": "<name>", "position": <n>}` — the
        argument every call to that callable passes (resolved where it is called);
        `{"enum": "<module>:<Class>", "format": "<prefix>{}<suffix>", "except": [...]}` —
        each member's value but the excepted ones, formatted."""

        function = self._function(site)
        expression = ast.parse(site.expression, mode="eval").body
        self._hints = hints or {}
        try:
            return self._resolve(expression, site.path, function, depth=0)
        finally:
            self._hints = {}

    def _function(self, site: Site) -> _Function | None:
        if site.function == "<module>":
            return None
        bare = site.function.rsplit(".", 1)[-1]
        for entry in self.index.functions.get(bare, []):
            if entry.module == site.path and entry.qualname == site.function:
                return entry
        raise LookupError(site.key)

    def _resolve(self, expr: ast.expr, module: str, function: _Function | None, depth: int) -> set[str]:
        if depth > 12:
            raise Unresolved("the pass-through chain is too deep")
        hint = self._hints.get(ast.unparse(expr))
        if hint is not None:
            return self._hinted(hint, depth)
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return {expr.value}
        if isinstance(expr, ast.Name):
            if _is_constant_name(expr.id):
                values = self.index.constant(module, expr.id)
                if not values:
                    raise Unresolved(f"{expr.id} names no module-level string")
                return values
            scope = function
            while scope is not None:
                if _is_parameter(scope.node, expr.id):
                    return self._parameter_values(scope, expr.id, depth)
                assigned = _assigned_values(scope.node, expr.id)
                if assigned:
                    values: set[str] = set()
                    for value in assigned:
                        values |= self._resolve(value, module, scope, depth + 1)
                    return values
                scope = scope.parent
            raise Unresolved(f"{expr.id} is neither a parameter nor assigned here")
        if isinstance(expr, ast.IfExp):
            return self._resolve(expr.body, module, function, depth + 1) | self._resolve(
                expr.orelse, module, function, depth + 1
            )
        raise Unresolved(f"{ast.unparse(expr)} is not read by the scan")

    def _hinted(self, hint: dict, depth: int) -> set[str]:
        if "argumentsOf" in hint:
            calls = self.index.calls.get(hint["argumentsOf"], [])
            if not calls:
                raise Unresolved(f"nothing calls {hint['argumentsOf']}")
            values: set[str] = set()
            for call, module, caller in calls:
                argument = next((k.value for k in call.keywords if k.arg == hint.get("parameter")), None)
                if argument is None and "position" in hint and hint["position"] < len(call.args):
                    argument = call.args[hint["position"]]
                if argument is None:
                    raise Unresolved(f"a call to {hint['argumentsOf']} in {module} passes no {hint.get('parameter')}")
                values |= self._resolve(argument, module, caller, depth + 1)
            return values
        if "enum" in hint:
            module_name, _, class_name = hint["enum"].partition(":")
            enum_class = getattr(importlib.import_module(module_name), class_name)
            excepted = set(hint.get("except", []))
            return {hint.get("format", "{}").format(m.value) for m in enum_class if m.value not in excepted}
        raise Unresolved(f"an unknown hint {hint}")

    def _parameter_values(self, function: _Function, name: str, depth: int) -> set[str]:
        values: set[str] = set()
        default = _default_of(function.node, name)
        calls = [
            (call, module, caller)
            for call, module, caller in self.index.calls.get(function.node.name, [])
            # A private helper is called from its own module only; a public one from anywhere.
            if module == function.module or not function.node.name.startswith("_")
        ]
        if not calls:
            raise Unresolved(f"nothing calls {function.qualname}")
        for call, module, caller in calls:
            argument = _argument_for(call, function.node, name)
            if argument is None:
                if default is None:
                    raise Unresolved(f"a call to {function.qualname} in {module} passes no {name}")
                values |= self._resolve(default, function.module, function.parent, depth + 1)
            else:
                values |= self._resolve(argument, module, caller, depth + 1)
        return values


def read_sites_json() -> dict[str, dict]:
    return json.loads(SITES_JSON.read_text(encoding="utf-8"))["sites"]


def listed_reasons() -> set[str]:
    """Every value the JSON says a refusal site can carry."""

    return {value for entry in read_sites_json().values() for value in entry.get("values", [])}
