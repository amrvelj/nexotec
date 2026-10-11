"""Every APIRoute an app serves, with the full path it is served at (KAN-241).

Under FastAPI 0.141 `app.routes` holds only the top level: the docs routes, one
`fastapi.routing._IncludedRouter` per included router and the app's own routes.
A guard that loops over `app.routes` alone sees no API route and passes whatever
the routes are — three did, until KAN-241. Walk the routes with this instead,
and have the guard assert it matched at least one route, so a future FastAPI
change cannot make it vacuous again without failing.

`tests/architecture/test_every_post_accepts_idempotency_key.py` pins that the
walk sees exactly the POST routes the OpenAPI publishes.
"""

from collections.abc import Iterable, Iterator
from typing import Any

from fastapi.routing import APIRoute


def api_routes(routes: Iterable[Any]) -> Iterator[tuple[str, APIRoute]]:
    """`(full path, route)` for every APIRoute in `routes` (pass `app.routes`)."""

    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, route
        elif hasattr(route, "effective_route_contexts"):  # fastapi.routing._IncludedRouter
            for context in route.effective_route_contexts():
                if isinstance(context.original_route, APIRoute):
                    yield context.path, context.original_route


def path_segments(path: str) -> list[str]:
    """`/v1/vehicle-mdm/{vehicle_id}/plates` -> ["v1", "vehicle-mdm", "{vehicle_id}", "plates"]."""

    return [segment for segment in path.split("/") if segment]
