"""Every POST honours Idempotency-Key (API conventions, ADR-035; KAN-119).

A POST honours the key when it is an `IdempotentRoute` (app/core/
idempotent_route.py) — replay, 409 on reuse with a different request, a claim
against concurrent twins — and its OpenAPI declares the header, optional.
A header declared by hand is not enough: the hand-copied pattern stored the
key only after the work had committed, so two concurrent submits both did it.

The walk flattens included routers. Under FastAPI 0.141 `app.routes` holds
only the top level (the docs routes and one `_IncludedRouter`); a loop over it
alone sees no API route and passes whatever the routes are. The first test
pins that the walk sees exactly the POSTs the OpenAPI publishes.
"""

from collections.abc import Iterator

from fastapi.routing import APIRoute

from app.core.idempotent_route import IDEMPOTENCY_KEY_HEADER, IdempotentRoute
from app.main import app

# Ratified by Anto on 2026-10-07 (KAN-119). Neither can take the route class:
# both set the session cookie on `Response`, which a replay would not restore.
# Both are already safe to repeat.
EXEMPT = {
    "/v1/auth/logout": "deletes the session cookie; no tenant to key on, and a second logout changes nothing",
    "/v1/auth/switch-dealership": "its result is the new session cookie; switching to the same dealership again "
    "yields the same session",
}

# KAN-266 converts one context per PR; this list shrinks to empty by the last.
# Do not add to it: a new POST goes on an IdempotentRoute router.
NOT_YET_CONVERTED = {
    "/v1/integrations/connections",
    "/v1/integrations/connections/{connection_id}/disable",
    "/v1/integrations/connections/{connection_id}/enable",
    "/v1/integrations/connections/{connection_id}/test",
    "/v1/integrations/providers",
    "/v1/inventory/reservations/{reservation_id}/release",
    "/v1/inventory/stock-items",
    "/v1/inventory/stock-items/{stock_item_id}/condition",
    "/v1/inventory/stock-items/{stock_item_id}/ledger-entries",
    "/v1/inventory/stock-items/{stock_item_id}/media",
    "/v1/inventory/stock-items/{stock_item_id}/media/reorder",
    "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/publish",
    "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/unpublish",
    "/v1/inventory/stock-items/{stock_item_id}/purchase",
    "/v1/inventory/stock-items/{stock_item_id}/reservations",
    "/v1/sales/contracts",
    "/v1/sales/contracts/{contract_id}/cancel",
    "/v1/sales/contracts/{contract_id}/confirm",
    "/v1/sales/contracts/{contract_id}/documents",
    "/v1/sales/contracts/{contract_id}/request-invoice",
    "/v1/sales/offers",
    "/v1/sales/offers/{offer_id}/cancel",
    "/v1/sales/offers/{offer_id}/copy",
    "/v1/sales/offers/{offer_id}/documents",
    "/v1/sales/offers/{offer_id}/finalize",
    "/v1/sales/offers/{offer_id}/trade-in",
    "/v1/sales/offers/{offer_id}/trade-in/valuation",
    "/v1/transactions",
    "/v1/transactions/{transaction_id}/cancel",
    "/v1/transactions/{transaction_id}/complete",
    "/v1/valuations",
    "/v1/valuations/{valuation_id}/mark-used",
}


def _api_routes(routes) -> Iterator[tuple[str, APIRoute]]:
    """Every APIRoute the app serves, with the full path it is served at."""

    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, route
        elif hasattr(route, "effective_route_contexts"):  # fastapi.routing._IncludedRouter
            for context in route.effective_route_contexts():
                if isinstance(context.original_route, APIRoute):
                    yield context.path, context.original_route


def _post_routes() -> dict[str, APIRoute]:
    return {path: route for path, route in _api_routes(app.routes) if "POST" in route.methods}


def _published_posts() -> dict[str, dict]:
    return {path: item["post"] for path, item in app.openapi()["paths"].items() if "post" in item}


def test_the_walk_sees_every_post_the_api_publishes():
    walked = set(_post_routes())
    assert walked, "the walk found no POST route at all — it is not reaching into the included routers"
    assert walked == set(_published_posts())


def test_every_post_honours_the_idempotency_key():
    published = _published_posts()
    failures = []
    for path, route in sorted(_post_routes().items()):
        if path in EXEMPT or path in NOT_YET_CONVERTED:
            continue
        header = next(
            (
                p
                for p in published[path].get("parameters", [])
                if p["in"] == "header" and p["name"] == IDEMPOTENCY_KEY_HEADER
            ),
            None,
        )
        if not isinstance(route, IdempotentRoute):
            failures.append(f"POST {path}: not an IdempotentRoute — build its router with route_class=IdempotentRoute")
        elif header is None:
            failures.append(f"POST {path}: the OpenAPI does not declare {IDEMPOTENCY_KEY_HEADER}")
        elif header.get("required"):
            failures.append(f"POST {path}: {IDEMPOTENCY_KEY_HEADER} is required — a breaking contract change")
    assert not failures, "\n".join(failures)


def test_the_allowlists_carry_no_stale_entry():
    posts = _post_routes()
    assert not EXEMPT.keys() & NOT_YET_CONVERTED
    for path in [*EXEMPT, *NOT_YET_CONVERTED]:
        assert path in posts, f"{path} is allow-listed but no longer a POST route — remove the entry"
        assert not isinstance(posts[path], IdempotentRoute), (
            f"POST {path} is an IdempotentRoute now — remove it from the allowlist"
        )
