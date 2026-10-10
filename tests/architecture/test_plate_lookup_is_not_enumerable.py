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

from app.vehicle.services import plate as plate_service

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


def test_no_route_lists_vehicle_plate_without_an_identifier():
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if "vehicle-plate" not in path and "vehicle_plate" not in path:
            continue
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods:
            continue
        # A safe route names its target in the path itself (a plate value,
        # a vehicle id) — {plate}/{vehicle_id}/etc. A route with no path
        # parameter at all is exactly the "list everything" shape this
        # test exists to forbid.
        has_path_param = "{" in path
        assert has_path_param, f"{path} is a GET route over plates with no identifier in the path"


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


def test_no_route_lists_the_plate_lookup_cache():
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        assert "plate-lookup" not in path and "plate_lookup" not in path, (
            f"{path} exposes the plate-lookup cache; it is read only through vehicle-identification"
        )


# KAN-231: `list_plates_for_vehicle` is targeted (one vehicle id), but the
# vehicle list pages through every vehicle without an identifier, so the two
# composed export the plate table in N+1 calls. The per-function guards above
# cannot see that composition. This one makes the two routes that hand out a
# vehicle's plate history (the Plates tab, a resolved search hit) read it only
# through `plate_read_guard`, which audits it and enforces the per-user limit;
# a new caller of the raw function anywhere else fails here. It matches the
# function's name only: a direct `select(VehiclePlate)` by vehicle id passes
# it — `services/lookup.py::_current_plate` behind `GET /v1/vehicle-mdm/lookup`
# (ADR-043, out of KAN-231's scope) is exactly that, and still hands out the
# current plate unaudited and unlimited until KAN-279 closes it.
_PLATE_HISTORY_READERS = {"app/vehicle/services/plate.py", "app/vehicle/services/plate_read_guard.py"}


def test_plate_history_is_read_only_through_the_audited_guard():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in _PLATE_HISTORY_READERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute | ast.alias):
                name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else node.name
                if name == "list_plates_for_vehicle":
                    offenders.append(f"{relative}:{getattr(node, 'lineno', '?')}")
    assert not offenders, (
        "plate history read outside plate_read_guard (unaudited, unlimited — KAN-231): " + ", ".join(offenders)
    )


def test_every_plate_read_route_goes_through_the_guard():
    """The two routes that hand out a vehicle's plates — the Plates tab and
    the resolved search hit — both import the guard's reader, never the raw
    function (the test above), and the guard itself still calls it."""

    import inspect as _inspect

    from app.vehicle.api import vehicle_mdm, vehicle_mdm_detail
    from app.vehicle.services import plate_read_guard

    assert "read_plate_history" in _inspect.getsource(vehicle_mdm_detail.list_plates)
    assert "read_plate_history" in _inspect.getsource(vehicle_mdm._search_hit)
    assert "list_plates_for_vehicle" in _inspect.getsource(plate_read_guard.read_plate_history)
