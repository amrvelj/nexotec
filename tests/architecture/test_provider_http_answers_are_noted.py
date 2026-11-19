"""KAN-767: every HTTP answer a provider adapter receives reaches the call log.

`integration_call_log.http_status` is «set for every provider response» only
because each adapter's one method that touches its HTTP client hands the
answer to `metering.ProviderRequest.note_response` before judging it (ADR-191).
Nothing else would notice a new adapter — or a new client call in an old
one — that forgets: its rows would quietly read NULL.

So, in every module of `app/integration/adapters/`: a function that sends an
HTTP request through a client — a call of `.request`, `.get`, `.post`,
`.put`, `.patch` or `.delete` on a name or attribute called `client`,
`http`, `_http` or `_client` — must itself call `note_response`. The
innermost enclosing function is the one judged, so a closure that sends
(the mail adapter's `_send`) must note the answer itself.
"""

import ast
from pathlib import Path

_ADAPTERS = Path(__file__).resolve().parents[2] / "app" / "integration" / "adapters"
_SEND_METHODS = {"request", "get", "post", "put", "patch", "delete"}
_CLIENT_NAMES = {"client", "http", "_http", "_client"}


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _sends_through_a_client(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _SEND_METHODS
        and _receiver_name(func.value) in _CLIENT_NAMES
    )


def _calls_note_response(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "note_response"


def _own_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """The calls in `function`'s own body — not in a function nested in it,
    which is judged on its own."""

    found: list[ast.Call] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def unnoted_senders(source: str) -> list[str]:
    """Functions in `source` that send through an HTTP client without
    calling `note_response`."""

    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = _own_calls(node)
        if any(_sends_through_a_client(c) for c in calls) and not any(_calls_note_response(c) for c in calls):
            offenders.append(f"{node.name} (line {node.lineno})")
    return offenders


def test_every_adapter_function_that_sends_http_notes_the_answer():
    offenders = {
        path.name: found
        for path in sorted(_ADAPTERS.glob("*.py"))
        if (found := unnoted_senders(path.read_text()))
    }
    assert offenders == {}, (
        "These adapter functions send an HTTP request without handing the answer to "
        f"ProviderRequest.note_response (KAN-767, ADR-191): {offenders}"
    )


def test_the_guard_sees_the_four_http_adapters():
    """The guard is not vacuous: the identAPI, the auto-i OAuth client, the
    mail and the AI adapters each have a sender it judges."""

    senders = set()
    for path in sorted(_ADAPTERS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                _sends_through_a_client(c) for c in _own_calls(node)
            ):
                senders.add(path.name)
    assert {"auto_i_dat_ident.py", "autoi_oauth.py", "mail_providers.py", "ai_providers.py"} <= senders


def test_the_guard_refuses_a_sender_that_forgets_and_judges_a_closure_on_its_own():
    forgets = (
        "def _send(self, request, url):\n"
        "    response = self._http.request('GET', url)\n"
        "    return response\n"
    )
    noted_outside_only = (
        "def _request(self, request):\n"
        "    def _send():\n"
        "        with httpx.Client() as client:\n"
        "            return client.post('u')\n"
        "    response = request.attempt(_send)\n"
        "    request.note_response(response.status_code)\n"
    )
    noted = (
        "def _post(self, request, url):\n"
        "    response = client.post(url)\n"
        "    request.note_response(response.status_code)\n"
        "    return response\n"
    )
    assert unnoted_senders(forgets) == ["_send (line 1)"]
    assert unnoted_senders(noted_outside_only) == ["_send (line 2)"]
    assert unnoted_senders(noted) == []
