"""KAN-467 — "the test that would have caught it": the plate register's write
service (`record_plate_assignment`) existed for two months with no caller —
no endpoint, no consumer, no import — so no plate could ever be recorded and
plate search could never find a car.

Every public function in `app/vehicle/services` that writes (calls
`db.add` / `add_all` / `delete` / `merge` / `flush` / `commit` on its
session) must be REACHED from production code outside its own module: named
by an endpoint, a consumer, `vehicle.public`, a daily job, another service or
a script under `scripts/` — or called by a function of its own module that is
itself reached that way. A name mentioned only by its own module's
unreached functions does not count. A write function nothing reaches is dead
code that looks like a feature.

The exceptions are listed with why and their ticket — each one a known gap,
never a way to park new code. An entry for a function that has gained a
caller, or no longer exists, fails too, so the list only shrinks.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES = _REPO_ROOT / "app" / "vehicle" / "services"
_PRODUCTION_ROOTS = (_REPO_ROOT / "app", _REPO_ROOT / "scripts")

_SESSION_NAMES = {"db", "session", "own"}
_WRITE_CALLS = {"add", "add_all", "delete", "merge", "flush", "commit"}

# function name -> why it has no production caller yet, and its ticket.
NO_CALLER_YET = {
    "assign_dealer_plate": "KAN-524: Händlerschild assignment (ADR-039's dealer-plate class) has no screen or API "
    "yet; KAN-467 leaves the dealer-plate model alone by its own do-not-touch",
    "merge_vehicles": "KAN-524: FR-V-12's merge has no screen yet — the Vehicle 360 overflow shows it disabled "
    "with its reason (vehicleRowMenu.tsx)",
}


def _is_session_write(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _WRITE_CALLS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _SESSION_NAMES
    )


def _write_services() -> dict[str, Path]:
    writers: dict[str, Path] = {}
    for path in sorted(_SERVICES.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and not node.name.startswith("_")
                and any(_is_session_write(inner) for inner in ast.walk(node))
            ):
                writers[node.name] = path
    return writers


def _names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            names.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            names.add(inner.attr)
        elif isinstance(inner, ast.alias):
            names.add(inner.asname or inner.name.rsplit(".", 1)[-1])
    return names


def _reached(writers: dict[str, Path]) -> set[str]:
    """The write services production code reaches: a function of a services
    module is an entry point when a module other than its own names it; from
    the entry points, every function of the same module they name is reached
    too, to a fixed point. Returns the writers among the reached."""

    modules = {}
    for path in sorted(_SERVICES.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        modules[path] = {node.name: _names_in(node) for node in tree.body if isinstance(node, ast.FunctionDef)}
    elsewhere: dict[Path, set[str]] = {path: set() for path in modules}
    for root in _PRODUCTION_ROOTS:
        for source in root.rglob("*.py"):
            names = _names_in(ast.parse(source.read_text(), filename=str(source)))
            for path, bodies in modules.items():
                if source != path:
                    elsewhere[path] |= names & bodies.keys()
    reached: set[str] = set()
    for path, bodies in modules.items():
        frontier, seen = list(elsewhere[path]), set()
        while frontier:
            name = frontier.pop()
            if name in seen:
                continue
            seen.add(name)
            frontier.extend(bodies.get(name, set()) & bodies.keys())
        reached |= {name for name in seen if writers.get(name) == path}
    return reached


def test_the_scan_sees_the_write_services():
    writers = _write_services()
    # The ones this ticket wired, and two long-standing ones: if these are
    # not found the scan is broken, not the code.
    assert {"record_plate", "end_plate", "record_odometer_reading", "add_accessory"} <= set(writers)


def test_every_vehicle_write_service_has_a_production_caller():
    writers = _write_services()
    reached = _reached(writers)
    uncalled = sorted(name for name in writers if name not in reached and name not in NO_CALLER_YET)
    assert not uncalled, (
        "write service(s) in app/vehicle/services with no production caller — no endpoint, consumer, public "
        f"function, job or script names them (KAN-467): {uncalled}"
    )


def test_every_exception_is_still_an_uncalled_write_service():
    writers = _write_services()
    reached = _reached(writers)
    stale = sorted(name for name in NO_CALLER_YET if name not in writers or name in reached)
    assert not stale, f"NO_CALLER_YET names a function that has a caller now or no longer writes: {stale}"


def test_the_guard_reports_a_write_service_without_a_caller(tmp_path, monkeypatch):
    """Proves the guard goes red: a writer nothing reaches is reported — also
    when only an unreached function of its own module names it."""

    services = tmp_path / "services"
    services.mkdir()
    (services / "orphan.py").write_text(
        "def record_orphan(db, row):\n    db.add(row)\n    db.flush()\n\n\n"
        "def unused_wrapper(db, row):\n    record_orphan(db, row)\n"
    )
    monkeypatch.setattr(__import__(__name__, fromlist=["_SERVICES"]), "_SERVICES", services)
    writers = _write_services()
    assert writers == {"record_orphan": services / "orphan.py"}
    assert "record_orphan" not in _reached(writers)
