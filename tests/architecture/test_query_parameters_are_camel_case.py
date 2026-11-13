"""Query parameters are camelCase (Anto, 2026-11-03, KAN-477 option A).

CLAUDE.md promised `updatedSince` for incremental sync while every endpoint
took `updated_since`: query parameters were spelled both ways (70 camelCase
names, 21 snake_case), so every new filter was a new naming decision and
integrators had to guess. The ruling: camelCase, like the JSON bodies. The
snake_case names already shipped keep working — renaming one without an
alias would break the API — and each gains its camelCase alias when its
endpoint is next touched; that change removes its entry below.

Read from the app's own OpenAPI, so a parameter declared any way
(`Query(alias=…)`, a plain argument, a dependency) is seen. An entry names
the endpoint too, so a shipped snake_case name cannot spread to a new route.
"""

import re

from app.main import app

CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*$")

# (parameter, "METHOD /path") — the snake_case parameters shipped before the
# ruling. Legacy: never added to, only removed from.
LEGACY_SNAKE_CASE = frozenset(
    {
        ("canton_not", "GET /v1/customers"),
        ("customer_type", "GET /v1/customers"),
        ("customer_type_not", "GET /v1/customers"),
        ("include_merged", "GET /v1/customers"),
        ("language_not", "GET /v1/customers"),
        ("lifecycle_status", "GET /v1/customers"),
        ("lifecycle_status_not", "GET /v1/customers"),
        ("updated_in_last_days", "GET /v1/customers"),
        ("updated_more_than_days_ago", "GET /v1/customers"),
        ("updated_since", "GET /v1/customers"),
        ("updated_since_start_of", "GET /v1/customers"),
        ("include_closed", "GET /v1/customers/{customer_id}/vehicles"),
        ("dealer_group_id", "GET /v1/dealerships"),
        ("condition_not", "GET /v1/inventory/stock-items"),
        ("lifecycle_status", "GET /v1/inventory/stock-items"),
        ("lifecycle_status_not", "GET /v1/inventory/stock-items"),
        ("provider_code", "GET /v1/integrations/connections"),
        ("tenant_id", "GET /v1/integrations/connections"),
        ("customer_id", "GET /v1/sales/contracts"),
        ("customer_id", "GET /v1/sales/offers"),
        ("customer_id", "GET /v1/transactions"),
        ("transaction_type", "GET /v1/transactions"),
        ("updated_since", "GET /v1/transactions"),
        ("vehicle_id", "GET /v1/transactions"),
        ("vehicle_number", "GET /v1/vehicle-mdm/lookup"),
        ("include_closed", "GET /v1/vehicle-mdm/{vehicle_id}/party-roles"),
    }
)


def _query_parameters() -> set[tuple[str, str]]:
    found = set()
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "query":
                    found.add((parameter["name"], f"{method.upper()} {path}"))
    return found


def test_the_walk_sees_query_parameters():
    names = {name for name, _ in _query_parameters()}
    assert {"cursor", "limit", "updated_since"} <= names


def test_every_new_query_parameter_is_camel_case():
    offenders = sorted(
        (name, endpoint)
        for name, endpoint in _query_parameters()
        if not CAMEL_CASE.match(name) and (name, endpoint) not in LEGACY_SNAKE_CASE
    )
    assert not offenders, (
        "Query parameters are camelCase (KAN-477): rename these, or give the endpoint's legacy "
        f"snake_case name a camelCase alias — {offenders}"
    )


def test_every_legacy_entry_is_still_live():
    """An entry whose parameter is gone (renamed, aliased, retired) is removed,
    so the list only shrinks and never excuses a name nobody ships."""

    stale = sorted(LEGACY_SNAKE_CASE - _query_parameters())
    assert not stale, stale
