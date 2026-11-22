"""Architecture test (KAN-217 PR-5, A-RULE-10, ADR-204): consent is decided by
Customers and never stubbed.

`integration.public.deliver_message` (and its pre-check `check_deliverable`)
take the consent verdict as `recipient=`. Python cannot stop a caller from
handing it a home-made object, so this test does: outside `app/integration`
every call passes `recipient=` a plain name that the same function bound to
a `message_recipient(...)` call — Customers' read, imported from
`app.customer.public`. A literal, an attribute, a dataclass built on the spot
or a name assigned from anything else fails here.

Also: the mail send alone (`send_customer_mail`) is not handed out by
`integration.public` any more, and nothing outside integration reaches it
or `services/messaging.send` (`messaging.send` sits behind
`deliver_message`)."""

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"
_GUARDED = {"deliver_message", "check_deliverable"}


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imports_read = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.customer.public"
        and any(alias.name == "message_recipient" for alias in node.names)
        for node in ast.walk(tree)
    )
    offences: list[str] = []
    for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        from_read: set[str] = set()
        other: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                value = node.value
                if isinstance(value, ast.Call) and _callee(value) == "message_recipient":
                    from_read.add(name)
                else:
                    other.add(name)
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and _callee(node) in _GUARDED):
                continue
            where = f"{path.relative_to(_APP.parent)}:{node.lineno}"
            recipient = next((kw.value for kw in node.keywords if kw.arg == "recipient"), None)
            if not imports_read:
                offences.append(f"{where}: the module does not import message_recipient from app.customer.public")
            elif not isinstance(recipient, ast.Name):
                offences.append(f"{where}: recipient= is not a name bound from message_recipient(...)")
            elif recipient.id not in from_read or recipient.id in other:
                offences.append(f"{where}: {recipient.id!r} is not (only) bound from message_recipient(...)")
    return offences


def _outside_integration() -> list[Path]:
    return [p for p in _APP.rglob("*.py") if "integration" not in p.relative_to(_APP).parts[:1]]


def test_every_delivery_outside_integration_passes_customers_verdict():
    offences = [offence for path in _outside_integration() for offence in _offences(path)]
    assert offences == []


def test_the_check_sees_a_call_site():
    """Proves the walk is not vacuous: Sales' send is found and passes."""

    send = _APP / "sales" / "services" / "message_send.py"
    tree = ast.parse(send.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee(n) in _GUARDED]
    assert len(calls) == 2 and _offences(send) == []


def test_a_home_made_verdict_is_refused(tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text(
        "from app.customer.public import message_recipient\n"
        "def send(db):\n"
        "    verdict = object()\n"
        "    deliver_message(db, recipient=verdict)\n"
        "def other(db):\n"
        "    deliver_message(db, recipient=Verdict(allowed=True))\n"
    )
    global _APP
    saved, _APP = _APP, tmp_path.parent
    try:
        assert len(_offences(fake)) == 2
    finally:
        _APP = saved


def test_nothing_outside_integration_sends_around_the_door():
    import app.integration.public as door

    assert not hasattr(door, "send_customer_mail")
    for path in _outside_integration():
        text = path.read_text()
        assert "send_customer_mail" not in text, path
        assert "integration.services.messaging" not in text, path
