"""Every `notify` call site in `app/` names a registered code (KAN-216;
Dealer Administration FR-A-47: "an event not in the catalogue cannot be
sent").

`notify` refuses an unknown code at run time; this test makes the same
refusal at review time, for every path — including the ones no test walks.
A call site must pass its code as a literal: `NotificationEventCode.<MEMBER>`
or a string constant, and the code must be declared in
`app/platform/services/notification_catalogue.py` (whose rows the platform
migration seeds — `tests/test_migrated_notification_catalogue.py`). A code
computed at run time cannot be checked, so it fails here too.

A call is `notify` imported from `app.platform.public` or
`app.platform.services.notifications`, or `.notify` on either module (any
alias). `security_notifications.notify` (the KAN-202 seam, which takes a
`SecurityNotice`) is not this function and is not walked.

`notify` is platform-internal: it writes platform's tables in the caller's
transaction, so a call from another context would be the shared
transaction rule 12 / ADR-047 forbid. The last test proves the ADR-047
architecture test refuses such a call as it stands — unclassified, and
under every classification that would let it through quietly.
"""

import ast
import shutil
from pathlib import Path

from app.platform.services.notification_catalogue import BY_CODE, NotificationEventCode

_APP = Path(__file__).resolve().parents[2] / "app"
_MODULES = {"app.platform.public", "app.platform.services.notifications"}
# `from app.platform import public` / `from app.platform.services import notifications`
_PARENT_NAMES = {("app.platform", "public"), ("app.platform.services", "notifications")}
_DEFINITION = _APP / "platform" / "services" / "notifications.py"


def _notify_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(names bound to the function, names bound to a module that has it)."""

    functions: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if node.module in _MODULES and alias.name == "notify":
                    functions.add(alias.asname or alias.name)
                if (node.module, alias.name) in _PARENT_NAMES:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODULES and alias.asname:
                    modules.add(alias.asname)
    return functions, modules


def _code_argument(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "event_code":
            return keyword.value
    return call.args[1] if len(call.args) >= 2 else None


def _problem(arg: ast.expr | None) -> str | None:
    if arg is None:
        return "no event code"
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return None if arg.value in BY_CODE else f"{arg.value!r} is not in the catalogue"
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id == "NotificationEventCode":
        return (
            None
            if arg.attr in NotificationEventCode.__members__
            else f"NotificationEventCode.{arg.attr} does not exist"
        )
    return f"the code is computed ({ast.unparse(arg)}) — name it literally"


def notify_calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    functions, modules = _notify_aliases(tree)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in functions) or (
            isinstance(func, ast.Attribute)
            and func.attr == "notify"
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            calls.append(node)
    return calls


def problems_in(source: str, label: str) -> list[str]:
    found = []
    for call in notify_calls(source):
        problem = _problem(_code_argument(call))
        if problem:
            found.append(f"{label}:{call.lineno}: {problem}")
    return found


def _call_sites() -> list[tuple[Path, str]]:
    return [(path, path.read_text()) for path in sorted(_APP.rglob("*.py")) if path != _DEFINITION]


def test_every_notify_call_site_names_a_registered_code():
    failures = []
    for path, source in _call_sites():
        failures += problems_in(source, str(path.relative_to(_APP.parent)))
    assert not failures, "\n".join(failures)


def test_the_walk_finds_the_security_familys_call_sites():
    # Nine in security_alerts.py (eight auth events, the recovery action) —
    # a walk that found none would pass the test above vacuously.
    assert sum(len(notify_calls(source)) for _, source in _call_sites()) >= 9


def test_an_unregistered_or_computed_code_is_reported():
    source = (
        "from app.platform.public import notify\n"
        "from app.platform.services import notifications as n\n"
        "notify(db, 'security.made_up', user_id=u, subject=s)\n"
        "n.notify(db, NotificationEventCode.NOT_A_MEMBER, user_id=u, subject=s)\n"
        "n.notify(db, code_from_somewhere, user_id=u, subject=s)\n"
        "notify(db, NotificationEventCode.ACCOUNT_LOCKED, user_id=u, subject=s)\n"
        "notify(db, event_code='security.account_locked', user_id=u, subject=s)\n"
    )
    found = problems_in(source, "probe.py")
    assert len(found) == 3, found
    assert "made_up" in found[0] and "NOT_A_MEMBER" in found[1] and "computed" in found[2]


_PROBE = (
    "from app.platform.public import NotificationEventCode, NotificationSubject, notify\n\n\n"
    "def probe(db, user_id):\n"
    "    notify(db, NotificationEventCode.ACCOUNT_LOCKED, user_id=user_id,\n"
    "           subject=NotificationSubject(type='customer', link='/customers'))\n"
)


def test_a_cross_context_notify_is_refused_by_the_adr_047_test(tmp_path):
    from tests.architecture import test_adr_047_no_shared_transaction as adr047

    own, reads = adr047._OWN_COMMIT_WRITES, adr047._READS
    shared = {**adr047._SHARED_TRANSACTION_EXCEPTIONS, **adr047._RULED_SHARED_TRANSACTIONS}
    symbol = "app.platform.public.notify"
    assert symbol not in own and symbol not in shared and symbol not in reads
    assert adr047._classification_problems(_APP, own, shared, reads) == []

    # The real tree, plus one module of another context that raises a notification.
    app = tmp_path / "app"
    shutil.copytree(_APP, app, ignore=shutil.ignore_patterns("__pycache__"))
    (app / "customer" / "services" / "kan216_probe.py").write_text(_PROBE)

    unclassified = adr047._classification_problems(app, own, shared, reads)
    assert any(symbol in p and "not classified" in p for p in unclassified), unclassified
    as_read = adr047._classification_problems(app, own, shared, reads | {symbol})
    assert any(symbol in p and "reaches a write" in p for p in as_read), as_read
    as_own_commit = {**own, symbol: ("app.platform.services.notifications.notify", "probe")}
    committed = adr047._classification_problems(app, as_own_commit, shared, reads)
    assert any(symbol in p and "no longer commits" in p for p in committed), committed
    # The shared-transaction exceptions are pinned by name, so they cannot quietly grow:
    # the tracked debt, and number allocation ruled by ADR-109 (KAN-207).
    assert set(shared) == {"app.sales.public.repoint_customer_transactions", "app.platform.public.allocate_number"}
