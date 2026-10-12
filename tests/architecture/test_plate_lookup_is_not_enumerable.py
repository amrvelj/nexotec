"""WP-5 PR-4, ADR-039: "test this, not a code review" — a plate already
held can be resolved, but the plate table can never be listed, browsed,
paged or exported. Two independent guards, so a future PR that adds either
kind of hole fails CI immediately:

1. Every public function in app.vehicle.services.plate that queries
   VehiclePlate must require an exact plate identifier — never expose a
   bare "give me everything" call.
2. No FastAPI route anywhere in the app resolves vehicle_plate rows
   without a plate (or vehicle) identifier in the path/query — introspects
   the live route table via app.main, not a hand-maintained list of
   "known-safe" paths.
"""

import inspect
import re

from fastapi import APIRouter, FastAPI

from app.vehicle.services import plate as plate_service
from tests.route_walk import api_routes, path_segments

# Functions in this module that legitimately don't take a bare plate+canton
# pair — they operate on a specific dealer plate (a different, tenant-owned
# asset), on the already-created row itself, or on a known VEHICLE id
# (WP-5 PR-9's Vehicle 360 Plates tab: "this car's own history", the
# targeted lookup from the other direction — not an open-ended query over
# every Kontrollschild).
_NOT_PLATE_LOOKUP_FUNCTIONS = {
    "current_dealer_plate_assignment",
    "assign_dealer_plate",
    "list_plates_for_vehicle",
}


def test_every_plate_query_function_requires_an_exact_identifier():
    for name, func in inspect.getmembers(plate_service, inspect.isfunction):
        if func.__module__ != plate_service.__name__:
            continue  # imported helper (and_, or_, select, ...), not defined here
        if name.startswith("_") or name in _NOT_PLATE_LOOKUP_FUNCTIONS:
            continue
        params = inspect.signature(func).parameters
        if name == "record_plate_assignment":
            assert "plate" in params and "canton" in params
            continue
        # Every remaining public function (today: resolve_plate) must take
        # an exact plate + canton — never a bare Session with nothing else,
        # which is what a list-everything function would look like.
        assert "plate" in params, f"{name} has no 'plate' parameter — is this an enumerable query?"
        assert "canton" in params, f"{name} has no 'canton' parameter — is this an enumerable query?"


def _is_plate_path(path: str) -> bool:
    """The guard's original rule — `vehicle-plate`/`vehicle_plate` anywhere in
    the path (`vehicle-plates.csv`, `vehicle-platenumbers`) — plus a plate token
    in any segment (`plates`, `plate-history`), but not `document-template`."""

    if "vehicle-plate" in path or "vehicle_plate" in path:
        return True
    return any(re.search(r"(^|[-_])plates?($|[^a-z])", segment) for segment in path_segments(path))


def _plate_list_violations(routes) -> tuple[int, list[str]]:
    """(GET routes over plates inspected, those with no identifier in the path).

    A safe route names its target in the path itself (a plate value, a
    vehicle id) — {plate}/{vehicle_id}/etc. A route with no path parameter
    at all is exactly the "list everything" shape this guard forbids."""

    inspected, violations = 0, []
    for path, route in api_routes(routes):
        if "GET" not in route.methods or not _is_plate_path(path):
            continue
        inspected += 1
        if "{" not in path:
            violations.append(f"{path} is a GET route over plates with no identifier in the path")
    return inspected, violations


def test_no_route_lists_vehicle_plate_without_an_identifier():
    from app.main import app

    inspected, violations = _plate_list_violations(app.routes)

    # KAN-241: today GET /v1/vehicle-mdm/{vehicle_id}/plates. Zero means the
    # walk no longer reaches the API routes, not that there is nothing to guard.
    assert inspected >= 1, "the guard inspected no plate route — it is not reaching the API routes"
    assert not violations, violations


def test_the_plate_list_guard_catches_a_route_listing_every_plate():
    """KAN-241, exit criterion 3: the guard fails on a violating route mounted
    the way the real ones are (an included router), not only on a flat list."""

    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.get("/vehicle-plates")
    def list_every_plate() -> list[str]:
        return []

    @router.get("/vehicle-plate-history")
    def export_plate_history() -> list[str]:
        return []

    @router.get("/vehicle-plates.csv")
    def export_every_plate() -> list[str]:
        return []

    @router.get("/dealerships/document-template")
    def not_a_plate_route() -> list[str]:
        return []

    @router.get("/vehicle-mdm/{vehicle_id}/plates")
    def plates_of_one_vehicle(vehicle_id: str) -> list[str]:
        return []

    app.include_router(router)

    inspected, violations = _plate_list_violations(app.routes)

    assert inspected == 4
    assert violations == [
        "/v1/vehicle-plates is a GET route over plates with no identifier in the path",
        "/v1/vehicle-plate-history is a GET route over plates with no identifier in the path",
        "/v1/vehicle-plates.csv is a GET route over plates with no identifier in the path",
    ]


# KAN-42 (C-D): the plate-lookup cache holds plate → Stammnummer pairs from
# `KontrollschildInfo`, the same personal-data risk (R-2), so it gets the
# same two guards. The only function that reads the table without an exact
# identifier is the TTL purge, which deletes and returns a count — it never
# hands a row to anyone.
_CACHE_FUNCTIONS_WITHOUT_AN_IDENTIFIER = {"purge_expired_plate_lookups"}


def test_every_plate_lookup_cache_function_requires_an_exact_identifier():
    from app.vehicle.services import plate_lookup_cache

    checked = 0
    for name, func in inspect.getmembers(plate_lookup_cache, inspect.isfunction):
        if func.__module__ != plate_lookup_cache.__name__ or name.startswith("_"):
            continue
        if name in _CACHE_FUNCTIONS_WITHOUT_AN_IDENTIFIER:
            assert inspect.signature(func).return_annotation is int, f"{name} must return a count, never rows"
            continue
        params = inspect.signature(func).parameters
        assert "tenant_id" in params, f"{name} reads the cache without a tenant — ADR-013"
        assert "plate" in params or "stammnummer" in params, f"{name} has no exact identifier — is it enumerable?"
        checked += 1
    assert checked >= 3


def _plate_lookup_cache_routes(routes) -> tuple[int, list[str]]:
    """(API routes inspected, those exposing the plate-lookup cache)."""

    inspected, exposing = 0, []
    for path, _route in api_routes(routes):
        inspected += 1
        if "plate-lookup" in path or "plate_lookup" in path:
            exposing.append(f"{path} exposes the plate-lookup cache; it is read only through vehicle-identification")
    return inspected, exposing


def test_no_route_lists_the_plate_lookup_cache():
    from app.main import app

    inspected, exposing = _plate_lookup_cache_routes(app.routes)

    # KAN-241: a guard that sees no route at all would pass whatever the routes are.
    assert inspected >= 100, f"the guard inspected only {inspected} routes — it is not reaching the API routes"
    assert not exposing, exposing


def test_the_plate_lookup_cache_guard_catches_a_route_exposing_the_cache():
    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.get("/plate-lookups")
    def list_plate_lookups() -> list[str]:
        return []

    app.include_router(router)

    inspected, exposing = _plate_lookup_cache_routes(app.routes)

    assert inspected == 1
    assert exposing == ["/v1/plate-lookups exposes the plate-lookup cache; it is read only through vehicle-identification"]


# KAN-231: `list_plates_for_vehicle` is targeted (one vehicle id), but the
# vehicle list pages through every vehicle without an identifier, so the two
# composed export the plate table in N+1 calls. The per-function guards above
# cannot see that composition. This one makes every route that hands out a
# vehicle's plates (the Plates tab, a resolved search hit and — KAN-279 — the
# shared identity lookup) read them only through `plate_read_guard`, which
# audits the read and enforces the per-user limit.
#
# KAN-279: matching the function's name alone let a direct
# `select(VehiclePlate).where(VehiclePlate.vehicle_id == …)` through — that is
# how `GET /v1/vehicle-mdm/lookup` handed out the current plate unaudited. So
# the guard now flags, anywhere in `app/` outside the two plate modules:
#   - any use of `list_plates_for_vehicle` (by name, attribute or alias),
#   - any use of the `VehiclePlate` or `VehiclePlateConflict` model (it
#     stores plate and canton too) as a value (`select(VehiclePlate)`,
#     `db.query(VehiclePlate)`, `db.get(plate.VehiclePlate, …)`,
#     `VehiclePlate.vehicle_id`, passing the class to a helper), followed
#     through `import … as …`,
#   - raw SQL text that reads `vehicle_plate` or `vehicle_plate_conflict` (FROM / JOIN).
# Type annotations and the import line itself are not reads. The few
# legitimate uses that hand out no plate are named below per enclosing
# function, so a new read added beside them in the same file still fails.
_PLATE_HISTORY_READERS = {"app/vehicle/services/plate.py", "app/vehicle/services/plate_read_guard.py"}
_PLATE_TABLE_USES_THAT_HAND_OUT_NO_PLATE = {
    # The model's own definition.
    ("app/vehicle/models/plate.py", "<module>"),
    # Registers every model for metadata; touches no row.
    ("app/model_registry.py", "<module>"),
    # FR-V-12 merge: re-points the duplicate's plate rows to the survivor in
    # one local transaction and returns a count; no plate leaves the service.
    ("app/vehicle/services/merge.py", "merge_vehicles"),
    # Nightly reconciliation (rule 10): checks recording_tenant_id against the
    # dealership table and reports dangling row ids, never a plate value.
    ("app/vehicle/reconciliation.py", "<module>"),
}
_RAW_PLATE_SQL = re.compile(r"\b(from|join)\s+vehicle_plate(_conflict)?\b", re.IGNORECASE)


def _plate_reads_outside_the_guard(sources: dict[str, str]) -> list[str]:
    """`file:line (function) what` for every plate-table read outside the
    guard, per the rule above. `sources` maps an `app/…` path to its text."""

    import ast

    offenders: list[str] = []
    for relative, source in sorted(sources.items()):
        if relative in _PLATE_HISTORY_READERS:
            continue
        tree = ast.parse(source)
        model_names, reader_names = {"VehiclePlate", "VehiclePlateConflict"}, {"list_plates_for_vehicle"}
        annotation_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "VehiclePlate":
                        model_names.add(alias.asname or alias.name)
                    elif alias.name == "list_plates_for_vehicle":
                        reader_names.add(alias.asname or alias.name)
            annotations: list[ast.expr | None] = []
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                every_arg = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                every_arg += [a for a in (node.args.vararg, node.args.kwarg) if a is not None]
                annotations += [node.returns, *(a.annotation for a in every_arg)]
            elif isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
            for annotation in annotations:
                if annotation is not None:
                    annotation_nodes.update(id(n) for n in ast.walk(annotation))

        offenders += _visit(tree, "<module>", relative, model_names | reader_names, annotation_nodes)
    return offenders


def _visit(node, function: str, relative: str, names: set[str], skipped: set[int]) -> list[str]:
    """Walk `node` keeping the enclosing function's name, for the per-function
    allowlist."""

    import ast

    found: list[str] = []
    for child in ast.iter_child_nodes(node):
        if id(child) in skipped:
            continue
        scope = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else function
        what = None
        if isinstance(child, ast.Name) and child.id in names:
            what = f"uses {child.id}"
        elif isinstance(child, ast.Attribute) and child.attr in names | {"VehiclePlate"}:
            what = f"uses .{child.attr}"
        elif isinstance(child, ast.Constant) and isinstance(child.value, str) and _RAW_PLATE_SQL.search(child.value):
            what = "reads vehicle_plate in raw SQL"
        if what and (relative, scope) not in _PLATE_TABLE_USES_THAT_HAND_OUT_NO_PLATE:
            found.append(f"{relative}:{getattr(child, 'lineno', '?')} ({scope}) {what}")
        found += _visit(child, scope, relative, names, skipped)
    return found


def test_plate_history_is_read_only_through_the_audited_guard():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    sources = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in (root / "app").rglob("*.py")
    }
    assert len(sources) > 100, "the guard read almost no source — it is not reaching app/"
    offenders = _plate_reads_outside_the_guard(sources)
    assert not offenders, (
        "plate table read outside plate_read_guard (unaudited, unlimited — KAN-231/KAN-279): " + ", ".join(offenders)
    )


def test_the_plate_read_guard_catches_every_shape_of_a_direct_read():
    """KAN-279: each shape the name-only guard missed, in a module of its
    own, is caught; the guard's own modules and type annotations are not, and
    an allowed use in one function does not excuse a read in another."""

    sources = {
        "app/vehicle/services/lookup_select.py": (
            "from sqlalchemy import select\n"
            "from app.vehicle.models.plate import VehiclePlate\n"
            "def current(db, vid):\n"
            "    return db.scalar(select(VehiclePlate).where(VehiclePlate.vehicle_id == vid))\n"
        ),
        "app/sales/services/aliased.py": (
            "from app.vehicle.models.plate import VehiclePlate as Kontrollschild\n"
            "def rows(db, vid):\n"
            "    return db.query(Kontrollschild).filter_by(vehicle_id=vid).all()\n"
        ),
        "app/vehicle/services/by_get.py": (
            "from app.vehicle.models import plate\n"
            "def one(db, pid):\n"
            "    return db.get(plate.VehiclePlate, pid)\n"
        ),
        "app/vehicle/services/raw.py": (
            "from sqlalchemy import text\n"
            "def raw(db, vid):\n"
            "    return db.execute(text('SELECT plate FROM vehicle_plate WHERE vehicle_id = :v'), {'v': vid})\n"
        ),
        "app/vehicle/api/history.py": (
            "from app.vehicle.services import plate\n"
            "def tab(db, vid):\n"
            "    return plate.list_plates_for_vehicle(db, vehicle_id=vid)\n"
        ),
        "app/vehicle/api/reader_alias.py": (
            "from app.vehicle.services.plate import list_plates_for_vehicle as history\n"
            "def tab(db, vid):\n"
            "    return history(db, vehicle_id=vid)\n"
        ),
        "app/vehicle/services/merge.py": (
            "from app.vehicle.models.plate import VehiclePlate\n"
            "def merge_vehicles(db):\n"
            "    return VehiclePlate.vehicle_id\n"
            "def sneaky(db, vid):\n"
            "    return db.scalars(select(VehiclePlate).where(VehiclePlate.vehicle_id == vid))\n"
        ),
        "app/vehicle/services/plate_read_guard.py": (
            "from app.vehicle.models.plate import VehiclePlate\n"
            "def read(db, vid):\n"
            "    return db.scalars(select(VehiclePlate).where(VehiclePlate.vehicle_id == vid))\n"
        ),
        "app/customer/services/conflicts.py": (
            "from app.vehicle.models.plate import VehiclePlateConflict\n"
            "def open_ones(db):\n"
            "    return db.scalars(select(VehiclePlateConflict))\n"
        ),
        "app/vehicle/services/raw_conflict.py": (
            "from sqlalchemy import text\n"
            "def raw(db):\n"
            "    return db.execute(text('SELECT * FROM vehicle_plate_conflict'))\n"
        ),
        "app/vehicle/api/typed.py": (
            "from app.vehicle.models.plate import VehiclePlate\n"
            "def show(rows: list[VehiclePlate]) -> VehiclePlate | None:\n"
            "    first: VehiclePlate | None = rows[0] if rows else None\n"
            "    return first\n"
        ),
    }

    offenders = _plate_reads_outside_the_guard(sources)

    assert {entry.split(":")[0] for entry in offenders} == {
        "app/vehicle/services/lookup_select.py",
        "app/sales/services/aliased.py",
        "app/vehicle/services/by_get.py",
        "app/vehicle/services/raw.py",
        "app/vehicle/api/history.py",
        "app/vehicle/api/reader_alias.py",
        "app/vehicle/services/merge.py",
        "app/customer/services/conflicts.py",
        "app/vehicle/services/raw_conflict.py",
    }, offenders
    merge_entries = [entry for entry in offenders if entry.startswith("app/vehicle/services/merge.py")]
    assert merge_entries and all("(sneaky)" in entry for entry in merge_entries), merge_entries


def test_every_plate_read_route_goes_through_the_guard():
    """The three routes that hand out a vehicle's plates — the Plates tab,
    the resolved search hit and the shared identity lookup (KAN-279) — all
    read through the guard's reader, never the raw function or the table
    (the test above), and the guard itself still calls it."""

    import inspect as _inspect

    from app.vehicle.api import vehicle_mdm, vehicle_mdm_detail
    from app.vehicle.services import lookup, plate_read_guard

    assert "read_plate_history" in _inspect.getsource(vehicle_mdm_detail.list_plates)
    assert "read_plate_history" in _inspect.getsource(vehicle_mdm._search_hit)
    assert "read_plate_history" in _inspect.getsource(lookup._to_shared_identity)
    assert "list_plates_for_vehicle" in _inspect.getsource(plate_read_guard.read_plate_history)
