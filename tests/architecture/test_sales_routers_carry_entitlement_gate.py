"""KAN-198 (D-A-04): every router of a licensed module carries the
entitlement gate, and carries it router-level, so no route can forget it.

The sales entitlement covers the sales, inventory and valuation contexts and
the configurator (vehicle configuration + identification). A route added to
one of those packages without the gate fails here; a route of another context
that gains the gate by mistake fails too, because a gate on master data would
hide contacts or vehicles from a service-only Betrieb (D-A-10).

KAN-810 (D-A-04; Aftersales slice 1, check 42) and KAN-808 (ADR-221 T3): the
workshop order's routers (`app.aftersales.api`) and the planning context's routers
(`app.planning.api`) carry the `aftersales` gate the same way — router-level, every
route, and no other module's gate. When Sales books through the planner, a
two-module gate needs its own ADR.

The walk uses tests.route_walk (KAN-241): under FastAPI 0.141 `app.routes`
and `api_v1_router.routes` hold `_IncludedRouter` objects, not APIRoutes, so a
naive loop sees nothing and passes vacuously. The counts below pin that the
walk saw the routes it claims to guard.
"""

import inspect

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.api import v1
from app.core.permissions import ModuleCode
from app.main import app
from tests.route_walk import api_routes

_GATED_PACKAGES = ("app.sales.api.", "app.inventory.api.", "app.valuation.api.")
_GATED_MODULES = {"app.vehicle.api.configuration", "app.vehicle.api.identification"}
_GATED_ROUTERS = 13  # KAN-555: the legacy transactions router is gone
# KAN-810: the service orders; KAN-808: the planning context's routers — both behind `aftersales`.
# Each package keeps its own floor of routes and its own router count, as each side had alone.
# KAN-818 adds Aftersales' second router, the service catalogue — behind the same gate.
_AFTERSALES_GATED = {
    "app.aftersales.api.": (16, 2),  # (at least this many routes, exactly this many routers)
    "app.planning.api.": (15, 1),
}
_AFTERSALES_PACKAGES = tuple(_AFTERSALES_GATED)


def _is_gated_module(module: str) -> bool:
    return module.startswith(_GATED_PACKAGES) or module in _GATED_MODULES


def _is_aftersales_module(module: str) -> bool:
    return module.startswith(_AFTERSALES_PACKAGES)


def _entitlement_of(dependency) -> ModuleCode | None:
    """The module a `require_entitlement(...)` dependency closes over, else None."""

    # A resolved Dependant (route.dependant.dependencies) or a bare
    # `Depends(...)` marker (router.dependencies): both carry the callable.
    call = getattr(dependency, "call", None) or getattr(dependency, "dependency", None)
    if getattr(call, "__qualname__", "").startswith("require_entitlement.") and inspect.isfunction(call):
        return inspect.getclosurevars(call).nonlocals.get("module")
    return None


def _gate_of(route: APIRoute) -> ModuleCode | None:
    for dependency in route.dependant.dependencies:
        module = _entitlement_of(dependency)
        if module is not None:
            return module
    return None


def _walk() -> list[tuple[str, APIRoute]]:
    routes = list(api_routes(app.routes))
    assert len(routes) > 100, "the route walk saw almost nothing — tests/route_walk.py no longer matches FastAPI"
    return routes


def test_every_route_of_a_sales_module_router_is_gated_with_sales():
    gated_routes = [(path, route) for path, route in _walk() if _is_gated_module(route.endpoint.__module__)]
    assert len(gated_routes) >= 70, len(gated_routes)
    ungated = [
        f"{sorted(route.methods)} {path}" for path, route in gated_routes if _gate_of(route) is not ModuleCode.SALES
    ]
    assert ungated == [], ungated


@pytest.mark.parametrize("package", sorted(_AFTERSALES_GATED))
def test_every_route_of_an_aftersales_or_planning_router_is_gated_with_aftersales(package):
    gated_routes = [(path, route) for path, route in _walk() if route.endpoint.__module__.startswith(package)]
    assert len(gated_routes) >= _AFTERSALES_GATED[package][0], len(gated_routes)
    ungated = [
        f"{sorted(route.methods)} {path}"
        for path, route in gated_routes
        if _gate_of(route) is not ModuleCode.AFTERSALES
    ]
    assert ungated == [], ungated


def test_no_other_route_carries_an_entitlement_gate():
    other_routes = [
        (path, route)
        for path, route in _walk()
        if not _is_gated_module(route.endpoint.__module__) and not _is_aftersales_module(route.endpoint.__module__)
    ]
    assert len(other_routes) >= 70, len(other_routes)
    gated = [f"{sorted(route.methods)} {path}" for path, route in other_routes if _gate_of(route) is not None]
    assert gated == [], gated


def test_the_gate_is_a_router_level_dependency():
    """Router-level, not per route: `APIRouter(dependencies=[...])` places the
    gate on every route of the router, ahead of the route's own dependencies."""

    checked = 0
    aftersales = dict.fromkeys(_AFTERSALES_GATED, 0)
    for name in dir(v1):
        router = getattr(v1, name)
        if not isinstance(router, APIRouter) or router is v1.api_v1_router:
            continue
        module = router.routes[0].endpoint.__module__ if router.routes else ""
        if _is_gated_module(module):
            assert any(_entitlement_of(d) is ModuleCode.SALES for d in router.dependencies), name
            checked += 1
        for package in _AFTERSALES_GATED:
            if module.startswith(package):
                assert any(_entitlement_of(d) is ModuleCode.AFTERSALES for d in router.dependencies), name
                aftersales[package] += 1
    assert checked == _GATED_ROUTERS, checked
    assert aftersales == {package: routers for package, (_, routers) in _AFTERSALES_GATED.items()}, aftersales
