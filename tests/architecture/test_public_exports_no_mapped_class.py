"""Architecture test (KAN-123): a context's `public` module hands out
functions, enums and plain values (dataclasses, Pydantic models), never a
SQLAlchemy mapped class.

With a mapped class in hand, another context can select, join, change and
flush the owner's rows, and the import-linter passes, because the import went
through `public`. That is how `group_listing.py`, `reservation_reconciliation.py`
and `VehicleParty.vehicle` happened (KAN-121, KAN-122, KAN-84). `public` is
the contract a remote service would offer (ADR-015), and a contract that hands
out table mappings is the database, not an API.

The exports went one exporting context per PR (KAN-123, exit criterion 3):
integration, valuation, inventory, sales, customer, vehicle, and platform
last (`DealerGroup`, `Dealership`, `Location`, `User`), which emptied the
shrink-only debt list this test kept until then. There is no list now: any
mapped class a `public` module binds fails by name. Every name a module binds
counts, not only `__all__`: `from app.<context>.public import <Model>` works
for any of them. Reconciliation modules keep reading other contexts' tables
(ADR-107) through the builders each owner hands out from its `public`
(`services/reconciliation_targets.py`), never through the class.
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


def _offenders(modules: list[types.ModuleType]) -> list[str]:
    return sorted(set().union(*(_mapped_exports(module) for module in modules)))


def test_no_public_module_exports_a_mapped_class() -> None:
    offenders = _offenders(_public_modules())
    assert offenders == [], (
        "A context's public module binds a mapped class. Hand out a function, an enum or a plain "
        f"value (a dataclass, a Pydantic model) instead (KAN-123): {offenders}"
    )


def test_platform_hands_out_values_only() -> None:
    # KAN-123 step 7: the last four went; the builders took their place.
    platform = importlib.import_module("app.platform.public")

    assert _mapped_exports(platform) == set()
    assert not {"DealerGroup", "Dealership", "Location", "User"} & set(vars(platform))
    assert {"dealership_reference_check", "user_reference_check"} <= set(platform.__all__)


def test_self_test_a_mapped_class_export_is_reported_by_name() -> None:
    # Red before green: a public module binding a mapped class — under its
    # own name or another, in __all__ or not — is reported, by name.
    from app.platform.identity.models.user import User
    from app.platform.organisation.models.dealership import Dealership

    fake = types.ModuleType("app.platform.public")
    fake.Dealership = Dealership  # type: ignore[attr-defined]
    fake.Staff = User  # type: ignore[attr-defined]
    fake.__all__ = ["Staff"]  # type: ignore[attr-defined]

    assert _offenders([*_public_modules(), fake]) == ["app.platform.public.Dealership", "app.platform.public.Staff"]


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
