"""KAN-467 — "the test that would have caught it": the plate register's write
service (`record_plate_assignment`) existed for two months with no caller —
no endpoint, no consumer, no import — so no plate could ever be recorded and
plate search could never find a car.

Every public function in `app/vehicle/services` that writes (calls
`db.add` / `add_all` / `delete` / `merge` / `flush` / `commit` on its
session) must be named somewhere in production code outside its own
definition: an endpoint, a consumer, `vehicle.public`, a daily job, another
service, or a script under `scripts/`. A write function nothing calls is
dead code that looks like a feature.

The exceptions are listed with why — each one a known gap with its ticket,
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

# function name -> why it has no production caller yet.
NO_CALLER_YET = {
    "assign_dealer_plate": "Händlerschild assignment (ADR-039's dealer-plate class) has no screen or API yet; "
    "KAN-467 leaves the dealer-plate model alone by its own do-not-touch",
    "merge_vehicles": "FR-V-12's merge has no screen yet — the Vehicle 360 overflow shows it disabled with its "
    "reason (vehicleRowMenu.tsx)",
    "sync_catalogue_variant": "loading a variant's details when it is opened is KAN-211 (the function's own "
    "docstring)",
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


def _referenced_names() -> set[str]:
    """Every name production code mentions — a call, an attribute, an
    import — outside the function definitions themselves."""

    names: set[str] = set()
    for root in _PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.alias):
                    names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


def test_the_scan_sees_the_write_services():
    writers = _write_services()
    # The ones this ticket wired, and two long-standing ones: if these are
    # not found the scan is broken, not the code.
    assert {"record_plate", "end_plate", "record_odometer_reading", "add_accessory"} <= set(writers)


def test_every_vehicle_write_service_has_a_production_caller():
    writers = _write_services()
    referenced = _referenced_names()
    uncalled = sorted(name for name in writers if name not in referenced and name not in NO_CALLER_YET)
    assert not uncalled, (
        "write service(s) in app/vehicle/services with no production caller — no endpoint, consumer, public "
        f"function, job or script names them (KAN-467): {uncalled}"
    )


def test_every_exception_is_still_an_uncalled_write_service():
    writers = _write_services()
    referenced = _referenced_names()
    stale = sorted(name for name in NO_CALLER_YET if name not in writers or name in referenced)
    assert not stale, f"NO_CALLER_YET names a function that has a caller now or no longer writes: {stale}"


def test_the_guard_reports_a_write_service_without_a_caller(tmp_path, monkeypatch):
    """Proves the guard goes red: a writer nothing calls is reported."""

    services = tmp_path / "services"
    services.mkdir()
    (services / "orphan.py").write_text("def record_orphan(db, row):\n    db.add(row)\n    db.flush()\n")
    monkeypatch.setattr(__import__(__name__, fromlist=["_SERVICES"]), "_SERVICES", services)
    writers = _write_services()
    assert writers == {"record_orphan": services / "orphan.py"}
    assert "record_orphan" not in _referenced_names()
