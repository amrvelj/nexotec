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
# asset) or on the already-created row itself, not on an open-ended query
# over every Kontrollschild.
_NOT_PLATE_LOOKUP_FUNCTIONS = {
    "current_dealer_plate_assignment",
    "assign_dealer_plate",
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
