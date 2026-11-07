"""Configurator C-0 / KAN-38 exit criterion 5, restated for the identAPI by
KAN-488 — "every call is rate-limited, retried and logged through the
existing gateway; no adapter method bypasses it." Test this, not a code
review.

**The identAPI adapter** (`adapters/auto_i_dat_ident.py`) reaches auto-i
through exactly one place: the private ``_request`` helper, the only method
that opens ``provider_request`` (one call-log row per attempt, KAN-486) and
goes through ``resilience.call_with_retry``; ``_send`` is the only method that
touches ``self._http`` and is called from ``_request`` alone. Everything else
— the access check, the six probes, and later the identification and detail
methods — is a parameterisation of ``_request``.

**The OAuth client** (`adapters/autoi_oauth.py`) has the same shape for the
authorization server: ``_send`` is the only method touching ``self._http``,
and ``_discover`` (the ``oauth_discovery`` row) and ``request_token`` (the
``oauth_token`` row) are the only two opening ``provider_request`` /
``call_with_retry`` and calling ``_send``.

**The SOAP adapter** (`adapters/auto_i_dat_soap.py`) keeps its guard until
KAN-550 removes it: ``_suchen`` is its one choke point.

Guard 2 (grep): neither real adapter class is constructed anywhere in ``app/``
but its own module's gateway factory, so every call inherits the circuit
breaker and the call log.
"""

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_ADAPTERS = _REPO / "app" / "integration" / "adapters"


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} class not found")


def _touches_self_attr(node: ast.AST, attr: str) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == attr and isinstance(n.value, ast.Name) and n.value.id == "self"
        for n in ast.walk(node)
    )


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name for n in ast.walk(node))


def _calls_method(node: ast.AST, method: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == method
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        for n in ast.walk(node)
    )


def _methods(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [n for n in cls.body if isinstance(n, ast.FunctionDef)]


def _assert_one_choke_point(cls: ast.ClassDef, *, choke: str, sender: str, client_attr: str) -> None:
    """`choke` alone opens the metered, retried request; `sender` alone
    touches the HTTP client, and only `choke` calls `sender`."""

    for method in _methods(cls):
        if method.name == "__init__":
            continue  # stores the injected client; never calls it
        touches_client = _touches_self_attr(method, client_attr)
        metered = _calls(method, "provider_request")
        retried = _calls(method, "call_with_retry")
        sends = _calls_method(method, sender)
        if method.name == sender:
            assert touches_client, f"{cls.name}.{sender} must be the method that uses the HTTP client"
        else:
            assert not touches_client, (
                f"{cls.name}.{method.name} touches self.{client_attr} directly — every provider request goes through {choke}"
            )
        if method.name == choke:
            assert metered, f"{cls.name}.{choke} must open provider_request (one call-log row per attempt, KAN-486)"
            assert retried, f"{cls.name}.{choke} must go through resilience.call_with_retry"
            assert sends, f"{cls.name}.{choke} must send through {sender}"
        else:
            assert not metered, f"{cls.name}.{method.name} opens provider_request — the meter belongs only in {choke}"
            assert not retried, f"{cls.name}.{method.name} calls call_with_retry — the retry layer belongs only in {choke}"
            assert not sends or method.name == sender, (
                f"{cls.name}.{method.name} calls {sender} directly — every provider request goes through {choke}"
            )


def test_the_identapi_adapter_sends_every_request_through_request():
    tree = ast.parse((_ADAPTERS / "auto_i_dat_ident.py").read_text())
    _assert_one_choke_point(
        _class(tree, "AutoIDatIdentAdapter"), choke="_request", sender="_send", client_attr="_http"
    )


def test_the_oauth_client_sends_only_through_its_two_metered_requests():
    tree = ast.parse((_ADAPTERS / "autoi_oauth.py").read_text())
    cls = _class(tree, "AutoiOAuthClient")
    for method in _methods(cls):
        if method.name == "__init__":
            continue
        touches_client = _touches_self_attr(method, "_http")
        if method.name == "_send":
            assert touches_client
        else:
            assert not touches_client, f"AutoiOAuthClient.{method.name} touches self._http — only _send may"
        metered = _calls(method, "provider_request")
        retried = _calls(method, "call_with_retry")
        sends = _calls_method(method, "_send")
        if method.name in ("request_token", "_discover"):
            assert metered and retried and sends, f"{method.name} must be a metered, retried request through _send"
        else:
            assert not metered and not retried and not sends, (
                f"AutoiOAuthClient.{method.name} sends, meters or retries outside request_token / _discover"
            )


def test_only_suchen_touches_the_soap_client_and_the_retry_layer():
    tree = ast.parse((_ADAPTERS / "auto_i_dat_soap.py").read_text())
    cls = _class(tree, "AutoIDatSoapAdapter")
    for method in _methods(cls):
        if method.name == "__init__":
            continue
        touches_client = _touches_self_attr(method, "_client")
        calls_retry = _calls(method, "call_with_retry")
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


def test_the_real_adapters_are_only_constructed_by_their_own_gateway_factory():
    owners = {
        "AutoIDatIdentAdapter(": "app/integration/adapters/auto_i_dat_ident.py",
        "AutoIDatSoapAdapter(": "app/integration/adapters/auto_i_dat_soap.py",
    }
    hits: list[str] = []
    for path in (_REPO / "app").rglob("*.py"):
        text = path.read_text()
        rel = path.relative_to(_REPO).as_posix()
        for needle, owner in owners.items():
            if needle in text and rel != owner:
                hits.append(f"{rel} builds {needle[:-1]}")
    assert hits == [], (
        f"a real adapter is constructed outside its own module ({hits}) — it must only be built by the "
        "gateway's registered factory, so every call inherits the circuit breaker + call log"
    )
