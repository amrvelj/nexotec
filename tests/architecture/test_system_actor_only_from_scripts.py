"""Architecture test (KAN-345; Anto, 2026-11-03, option A): the system path
into a create service is for seeds and scripts only.

`create_dealership` and `create_user` accept `actor_id=None` with a
`system_reason` (`app.platform.reference.services.system_writes.system_write_reason`):
the rows and the `create` audit row then carry no actor, and the reason names
the system that wrote them. An API call must never take that path — a request
always has a person behind it, and the audit row names them. So nothing in
`app/` passes `system_reason=` (the helper's own definition and the two
services' parameters aside), and the two routes that create pass the
principal's id. `scripts/` may.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_SYSTEM_PATH_SERVICES = {"create_dealership", "create_user"}
# Where the two services live: only there may a call hand the reason on.
_SERVICE_FILES = {"app/platform/services/dealership.py", "app/platform/services/user.py"}


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            yield name, node


def _forwarded_inside_the_services(tree: ast.AST) -> set[int]:
    """Calls inside `create_dealership` / `create_user` themselves: the system
    path's own plumbing (the reason handed on to the first location's audit
    row), not a caller taking it."""

    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _SYSTEM_PATH_SERVICES:
            inside |= {id(call) for _, call in _calls(node)}
    return inside


def _offences(source: str, path: str = "app/probe.py") -> list[str]:
    out: list[str] = []
    tree = ast.parse(source)
    forwarded = _forwarded_inside_the_services(tree) if path in _SERVICE_FILES else set()
    for name, call in _calls(tree):
        if id(call) in forwarded:
            continue
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        if "system_reason" in keywords:
            out.append(f"{call.lineno}: {name}(system_reason=…)")
        if name in _SYSTEM_PATH_SERVICES and any(kw.arg is None for kw in call.keywords):
            out.append(f"{call.lineno}: {name}(**…) cannot be judged")
        if name in _SYSTEM_PATH_SERVICES:
            actor = keywords.get("actor_id")
            if actor is None:
                out.append(f"{call.lineno}: {name}() without actor_id=")
            elif isinstance(actor, ast.Constant) and actor.value is None:
                out.append(f"{call.lineno}: {name}(actor_id=None)")
    return out


def test_no_module_in_app_takes_the_system_path():
    files = sorted(_APP_ROOT.rglob("*.py"))
    assert any(path.name == "dealerships.py" for path in files)  # an empty walk would pass vacuously
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{offence}"
        for path in files
        for offence in _offences(path.read_text(), str(path.relative_to(_REPO_ROOT)))
    ]
    assert offenders == [], "Only seeds and scripts write without an actor (KAN-345):\n" + "\n".join(offenders)


def test_the_create_routes_pass_the_principals_id():
    # KAN-668: create_user's route moved to dealership_users.py; create_dealership's stayed.
    api = _APP_ROOT / "platform" / "api"
    actors = [
        ast.unparse(kw.value)
        for source in ((api / "dealerships.py").read_text(), (api / "dealership_users.py").read_text())
        for name, call in _calls(ast.parse(source))
        if name in _SYSTEM_PATH_SERVICES
        for kw in call.keywords
        if kw.arg == "actor_id"
    ]
    assert actors == ["principal.user_id", "principal.user_id"]


def test_matches_the_shapes_it_exists_to_stop():
    assert _offences("svc.create_user(db, actor_id=None, system_reason='x')") == [
        "1: create_user(system_reason=…)",
        "1: create_user(actor_id=None)",
    ]
    assert _offences("create_dealership(db, data=d)") == ["1: create_dealership() without actor_id="]
    assert _offences("create_dealership(db, data=d, actor_id=principal.user_id)") == []
    # The services hand their own reason on; a caller elsewhere may not.
    forwarding = "def create_dealership(db, *, actor_id, system_reason=None):\n    f(system_reason=system_reason)"
    assert _offences(forwarding, "app/platform/services/dealership.py") == []
    # … and only there: a route handler of the same name is judged like any caller.
    route = "def create_user(body, principal):\n    svc.create_user(db, actor_id=None, system_reason='api')"
    assert _offences(route, "app/platform/api/dealerships.py") == [
        "2: create_user(system_reason=…)",
        "2: create_user(actor_id=None)",
    ]
    assert _offences("def other():\n    f(system_reason='x')") == ["2: f(system_reason=…)"]
    assert _offences("create_user(db, actor_id=principal.user_id, **{'system_reason': 'x'})") == [
        "1: create_user(**…) cannot be judged"
    ]


def test_the_seed_takes_it():
    source = (_REPO_ROOT / "scripts" / "seed_staging_demo.py").read_text()
    assert len([o for o in _offences(source) if "system_reason" in o]) == 3
