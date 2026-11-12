"""KAN-337 — the VIN-to-vehicle-mdm resolution is Vehicle's, and nobody
keeps a copy of it.

KAN-257 ruled that a context resolving a VIN hands vehicle-mdm a session of
its own (its commit would land the caller's pending work, its race guard's
rollback would discard it) and takes the winner's record when it loses the
insert race. That rule was copied three times (Stock, the Sales trade-in,
the valuation) before KAN-189 retired two; since KAN-337 it lives once, in
`app/vehicle/services/vin_resolution.py`, offered as
`vehicle.public.resolve_vehicle_mdm_by_vin`.

What this proves, by reading the source (`ast`, no database):

1. No module under `app/` outside the vehicle context names
   `create_or_get_vehicle_mdm` — the found-or-created call that runs on the
   session it is handed. Another context reaches vehicle-mdm creation only
   through `resolve_vehicle_mdm_by_vin`, which opens Vehicle's own session.
2. No module under `app/` outside the vehicle context defines a
   `_lost_the_insert_race` (or any function of that name) — the predicate
   that reads vehicle-mdm's refusal belongs beside the guard that raises it.

`scripts/` and `tests/` are not scanned: a script or a test set-up may
create a record on a session it owns.
"""

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent.parent / "app"
_OWN_SESSION_BYPASS = "create_or_get_vehicle_mdm"
_RACE_PREDICATE = "_lost_the_insert_race"


def _names_used(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _functions_defined(tree: ast.AST) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def _problems(app: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(app.rglob("*.py")):
        relative = path.relative_to(app)
        if relative.parts[0] == "vehicle":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = f"app/{relative.as_posix()}"
        if _OWN_SESSION_BYPASS in _names_used(tree):
            problems.append(
                f"{where} names {_OWN_SESSION_BYPASS}: resolve a VIN through "
                "vehicle.public.resolve_vehicle_mdm_by_vin, which runs it on Vehicle's own session (KAN-337)"
            )
        if _RACE_PREDICATE in _functions_defined(tree):
            problems.append(
                f"{where} defines {_RACE_PREDICATE}: vehicle-mdm's insert race is resolved by "
                "vehicle.public.resolve_vehicle_mdm_by_vin, never by a copy (KAN-337)"
            )
    return problems


def test_only_vehicle_resolves_a_vin_into_vehicle_mdm() -> None:
    assert _problems(_APP) == []


def test_the_resolution_is_offered_by_vehicle_public() -> None:
    from app.vehicle import public

    assert "resolve_vehicle_mdm_by_vin" in public.__all__
    assert "ResolvedVehicleMdm" in public.__all__


# --- self-tests: the scan sees what it is meant to see ----------------------


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    app = tmp_path / "app"
    for name, source in files.items():
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return app


def test_self_test_a_clean_tree_passes(tmp_path: Path) -> None:
    app = _tree(
        tmp_path,
        {
            "inventory/services/pipeline.py": (
                "from app.vehicle.public import resolve_vehicle_mdm_by_vin\n"
                "def promote(db, vin):\n    return resolve_vehicle_mdm_by_vin(db, vin=vin)\n"
            ),
            "vehicle/services/vin_resolution.py": (
                "def _lost_the_insert_race(exc, vin):\n    return True\n"
                "def resolve(db, vin):\n    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm\n"
            ),
        },
    )
    assert _problems(app) == []


def test_self_test_another_context_calling_the_bypass_fails(tmp_path: Path) -> None:
    app = _tree(
        tmp_path,
        {
            "sales/services/trade_in.py": (
                "from app.vehicle.public import create_or_get_vehicle_mdm\n"
                "def resolve(db, vin):\n    return create_or_get_vehicle_mdm(db, vin=vin)\n"
            )
        },
    )
    problems = _problems(app)
    assert len(problems) == 1 and "app/sales/services/trade_in.py names create_or_get_vehicle_mdm" in problems[0]


def test_self_test_a_call_through_the_module_attribute_fails(tmp_path: Path) -> None:
    app = _tree(
        tmp_path,
        {
            "valuation/services/valuation.py": (
                "from app.vehicle import public as vehicle_public\n"
                "def resolve(db, vin):\n    return vehicle_public.create_or_get_vehicle_mdm(db, vin=vin)\n"
            )
        },
    )
    assert any("create_or_get_vehicle_mdm" in problem for problem in _problems(app))


def test_self_test_a_copied_race_predicate_fails(tmp_path: Path) -> None:
    app = _tree(
        tmp_path,
        {"valuation/services/valuation.py": "def _lost_the_insert_race(exc, vin):\n    return False\n"},
    )
    problems = _problems(app)
    assert len(problems) == 1 and "defines _lost_the_insert_race" in problems[0]
