"""Architecture test (KAN-264): every idempotency key the server writes
itself has the reserved shape `<context>.<action>:…`.

Client keys and internal keys share `idempotency_record`'s one primary key,
and `app/core/idempotent_route.py` refuses (422) a client key matching
`RESERVED_KEY_PATTERN`. That protects an internal key only while the key
matches the pattern, so this test reads the source (`ast`, no database) of
`app/` and `scripts/` and checks every value passed as an idempotency key:

- to any call, as the keyword `idempotency_key=` (`reserve_for_contract`,
  `release`, `set_valuation_ref`, the `IdempotencyRecord` row itself, …);
- to `find_cached_response` / `store_response`, as the keyword `key=`.

A string literal or f-string must match the reserved pattern by its literal
prefix (an f-string must start with the `<context>.<action>:` text, before
its first placeholder). Anything else is refused by name unless it is one of
the forwarding shapes `_forwarded` lists — a parameter handed through
unchanged, `None` (no key), or the client's own key in the route class. A
new internal key that falls outside the grammar, or one built in a way this
test cannot read, fails here.

It also pins the grammar's list of contexts to the packages under `app/`, so
a thirteenth context cannot be left out of it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.idempotent_route import RESERVED_KEY_CONTEXTS, is_reserved_idempotency_key

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCANNED = (_REPO_ROOT / "app", _REPO_ROOT / "scripts")

# Packages under app/ that are wiring, not contexts (as in
# test_adr_047_no_shared_transaction.py).
_NOT_A_CONTEXT = {"core", "api"}

_KEY_KEYWORD_ON = {"find_cached_response": "key", "store_response": "key"}


def _forwarded(file: Path, value: ast.expr) -> bool:
    """A value that is not a key literal, but passes a key through: the
    caller's parameter, no key at all, or the client's header value in the
    route class (which the route refuses when it is reserved-shaped)."""

    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.Name) and value.id == "idempotency_key":
        return True
    if file == _REPO_ROOT / "app" / "core" / "idempotency.py" and isinstance(value, ast.Name) and value.id == "key":
        return True  # store_response writing the row under the key it was given
    return (
        file == _REPO_ROOT / "app" / "core" / "idempotent_route.py"
        and isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "self"
        and value.attr == "key"
    )


def _literal_prefix(value: ast.expr) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        prefix = ""
        for part in value.values:
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                break
            prefix += part.value
        return prefix
    return None


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _key_arguments() -> list[tuple[Path, int, ast.expr]]:
    found = []
    for root in _SCANNED:
        for file in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(file.read_text(), filename=str(file))):
                if not isinstance(node, ast.Call):
                    continue
                key_keyword = _KEY_KEYWORD_ON.get(_callee(node) or "")
                for keyword in node.keywords:
                    if keyword.arg == "idempotency_key" or (key_keyword and keyword.arg == key_keyword):
                        found.append((file, node.lineno, keyword.value))
    return found


def test_every_internal_key_has_the_reserved_shape():
    literal_prefixes = []
    refused = []
    for file, line, value in _key_arguments():
        where = f"{file.relative_to(_REPO_ROOT)}:{line}"
        if _forwarded(file, value):
            continue
        prefix = _literal_prefix(value)
        if prefix is None:
            refused.append(f"{where}: an idempotency key built as `{ast.unparse(value)}` — write it as a literal "
                           "or f-string starting `<context>.<action>:`")
        elif not is_reserved_idempotency_key(prefix):
            refused.append(f"{where}: `{ast.unparse(value)}` is outside the reserved grammar (RESERVED_KEY_PATTERN)")
        else:
            literal_prefixes.append(prefix)

    assert refused == [], "\n".join(refused)
    # Not vacuous: the internal keys known when KAN-264 was built are seen.
    assert {
        "sales.contract.confirm:",
        "sales.contract.confirm-compensate:",
        "sales.contract.cancel:",
        "inventory.reservation_sweep:",
    } <= set(literal_prefixes)


def test_a_key_outside_the_grammar_would_be_caught():
    """The scan's own check, on the shapes it must refuse."""

    for outside in ("contract.confirm:", "sales_contract_confirm:", "Sales.contract.confirm:", "sales.contract"):
        assert not is_reserved_idempotency_key(outside), outside
    assert _literal_prefix(ast.parse('f"{prefix}:{x}"', mode="eval").body) == ""


def test_the_grammar_names_exactly_the_contexts_under_app():
    packages = {
        child.name
        for child in (_REPO_ROOT / "app").iterdir()
        if child.is_dir() and (child / "__init__.py").exists()
    }

    assert set(RESERVED_KEY_CONTEXTS) == packages - _NOT_A_CONTEXT
    assert len(RESERVED_KEY_CONTEXTS) == 12
