"""Architecture test (KAN-123): a context's `public` module hands out
functions, enums and plain values (dataclasses, Pydantic models), never a
SQLAlchemy mapped class.

With a mapped class in hand, another context can select, join, change and
flush the owner's rows, and the import-linter passes, because the import went
through `public`. That is how `group_listing.py`, `reservation_reconciliation.py`
and `VehicleParty.vehicle` happened (KAN-121, KAN-122, KAN-84). `public` is
the contract a remote service would offer (ADR-015), and a contract that hands
out table mappings is the database, not an API.

The exports go one exporting context per PR (KAN-123, exit criterion 3).
Until the last one, `_DEBT` names each mapped class a `public` module still
binds, and it may only shrink: a new mapped-class export fails by name, and so
does an entry whose class is no longer there (the PR that removes the export
deletes its line). Every name a module binds counts, not only `__all__`:
`from app.<context>.public import <Model>` works for any of them. Reconciliation
modules keep reading other contexts' tables (ADR-107); when their context's
entries go, they take the class from where ADR-107 lets them.
"""

import importlib
import types
import uuid
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Mapper

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# The twelve bounded contexts (CLAUDE.md); test_adr_047_no_shared_transaction.py
# fails when app/ gains a package that is neither one of these nor wiring.
_CONTEXTS = (
    "platform",
    "customer",
    "vehicle",
    "sales",
    "inventory",
    "valuation",
    "integration",
    "aftersales",
    "parts",
    "finance",
    "reporting",
    "compliance",
)

# Shrink-only. Integration's two (IntegrationConnection, IntegrationEntitlement)
# went first, in KAN-123's first PR: `ConnectionRef` and `EntitlementState` took
# their place. Sales' `Transaction` went with its table (KAN-555). Valuation's
# `Valuation` went in the second (ADR-162), inventory's `StockItem` in the third.
_DEBT = frozenset(
    {
        "app.customer.public.Customer",
        "app.customer.public.VehicleParty",
        "app.platform.public.DealerGroup",
        "app.platform.public.Dealership",
        "app.platform.public.Location",
        "app.platform.public.User",
        "app.vehicle.public.VehicleConfiguration",
        "app.vehicle.public.VehicleMdm",
    }
)


def _is_mapped_class(value: object) -> bool:
    return isinstance(value, type) and isinstance(inspect(value, raiseerr=False), Mapper)


def _mapped_exports(module: types.ModuleType) -> set[str]:
    return {f"{module.__name__}.{name}" for name, value in vars(module).items() if _is_mapped_class(value)}


def _public_modules() -> list[types.ModuleType]:
    return [
        importlib.import_module(f"app.{context}.public")
        for context in _CONTEXTS
        if (_APP_ROOT / context / "public.py").is_file()
    ]


def _exported() -> set[str]:
    return set().union(*(_mapped_exports(module) for module in _public_modules()))


def test_no_public_module_exports_a_mapped_class_beyond_the_debt() -> None:
    new = sorted(_exported() - _DEBT)
    assert new == [], (
        "A context's public module binds a mapped class. Hand out a function, an enum or a plain "
        f"value (a dataclass, a Pydantic model) instead (KAN-123): {new}"
    )


def test_the_debt_only_shrinks() -> None:
    gone = sorted(_DEBT - _exported())
    assert gone == [], f"No longer exported: delete these lines from _DEBT in the same PR: {gone}"


def test_integration_hands_out_values_only() -> None:
    integration = importlib.import_module("app.integration.public")

    assert _mapped_exports(integration) == set()
    assert {"ConnectionRef", "EntitlementState"} <= set(integration.__all__)
    assert not {"IntegrationConnection", "IntegrationEntitlement"} & set(vars(integration))


def test_self_test_a_mapped_class_is_found_and_nothing_else_is() -> None:
    from app.integration.models.connection import ConnectionStatus, IntegrationConnection
    from app.integration.services.connection_refs import ConnectionRef, get_enabled_connection

    fake = types.ModuleType("app.fake.public")
    fake.Row = IntegrationConnection  # type: ignore[attr-defined]
    fake.Ref = ConnectionRef  # type: ignore[attr-defined]
    fake.Status = ConnectionStatus  # type: ignore[attr-defined]
    fake.lookup = get_enabled_connection  # type: ignore[attr-defined]
    fake.instance = ConnectionRef(id=uuid.uuid4(), tenant_id=uuid.uuid4())  # type: ignore[attr-defined]

    assert _mapped_exports(fake) == {"app.fake.public.Row"}


def test_self_test_every_context_with_a_public_module_is_read() -> None:
    names = {module.__name__ for module in _public_modules()}

    assert {"app.integration.public", "app.platform.public", "app.vehicle.public"} <= names
    assert all(name.removeprefix("app.").removesuffix(".public") in _CONTEXTS for name in names)
