"""Configurator C-0 / KAN-38 exit criterion 5 — "every call is rate-
limited, retried and logged through the existing gateway; no adapter
method bypasses it." Test this, not a code review.

The real adapter reaches auto-i-dat through exactly one place: the
private ``_suchen`` helper, which is the only method that touches
``self._client`` and the only one that goes through
``resilience.call_with_retry``. Everything else — the seven public
wrappers, valuation, forecast — is pure parsing over ``_suchen``'s
result. The gateway's per-connection circuit breaker + call-log wrap the
whole capability call the adapter sits inside
(``services/gateway.py::call_capability``), and the adapter is only ever
constructed by the gateway's own factory.

Guard 1 (AST): no ``self._client`` access outside ``_suchen``; ``_suchen``
is the only method calling ``call_with_retry``.
Guard 2 (grep): ``AutoIDatSoapAdapter(...)`` is constructed nowhere in
``app/`` except its own module's ``_build_real_adapter``.
"""

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_ADAPTER = _REPO / "app" / "integration" / "adapters" / "auto_i_dat_soap.py"


def _adapter_class(tree: ast.Module) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AutoIDatSoapAdapter":
            return node
    raise AssertionError("AutoIDatSoapAdapter class not found")


def test_only_suchen_touches_the_soap_client_and_the_retry_layer():
    tree = ast.parse(_ADAPTER.read_text())
    cls = _adapter_class(tree)

    for method in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
        if method.name == "__init__":
            continue  # stores the injected client; never calls it
        touches_client = any(
            isinstance(node, ast.Attribute)
            and node.attr == "_client"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            for node in ast.walk(method)
        )
        calls_retry = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "call_with_retry"
            for node in ast.walk(method)
        )
        if method.name == "_suchen":
            assert touches_client, "_suchen must be the method that calls the SOAP client"
            assert calls_retry, "_suchen must go through resilience.call_with_retry"
        else:
            assert not touches_client, (
                f"{method.name} touches self._client directly — every provider call must go through _suchen"
            )
            assert not calls_retry, (
                f"{method.name} calls call_with_retry directly — the retry layer belongs only in _suchen"
            )


def test_the_real_adapter_is_only_constructed_by_its_own_gateway_factory():
    hits: list[str] = []
    for path in (_REPO / "app").rglob("*.py"):
        text = path.read_text()
        if "AutoIDatSoapAdapter(" not in text:
            continue
        rel = path.relative_to(_REPO).as_posix()
        if rel != "app/integration/adapters/auto_i_dat_soap.py":
            hits.append(rel)
    assert hits == [], (
        f"AutoIDatSoapAdapter is constructed outside its own module ({hits}) — it must only be built by the "
        "gateway's registered factory, so every call inherits the circuit breaker + call log"
    )
