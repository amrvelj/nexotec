"""Architecture test (KAN-628, ADR-131): synchronous calls between bounded
contexts go one way only — down the layer order — and today's upward imports
are a debt list that may only shrink.

import-linter (CLAUDE.md rule 3, `pyproject.toml`) makes `<context>.public`
the only door into a context. It says nothing about *direction*: before this
test the contexts called each other in circles (platform ↔ customer,
inventory ↔ sales, valuation ↔ sales, vehicle ↔ customer, …). A context that
calls upward cannot be extracted or tested without the contexts above it,
which is what ADR-015's «extraction is triggered, not scheduled» assumes.

**The layer order (ADR-131), lowest first** — a context may import the
`public` of a context with a LOWER rank, never one with the same or a higher
rank:

    core · platform · integration · vehicle · customer · valuation ·
    inventory (parts beside it) · sales (aftersales beside it) · finance ·
    reporting and compliance (on top, consuming events only)

`customer` sits above `vehicle`: `VehicleParty` is customer-owned data about a
vehicle, and vehicle master data serves every context above it (ADR-131 gives
the reasoning). Contexts that share a rank ("beside") are peers and import
each other in neither direction. The composition roots directly under `app/`
(`app/api`, `app/main.py`, `app/worker.py`, `app/model_registry.py`,
`app/reconciliation_runner.py`, `app/ownership_registry.py`) are above every
context: they may import any context, and no context may import them.
`app/core` is below every context and imports none (`app/db.py` counts as
core). What a lower context needs to know about a higher one arrives as an
event and is kept as a local projection, or the higher context passes it in.

**Exempt, by path (ADR-107):** each context's nightly reconciliation module,
`app/<context>/reconciliation.py` — checking integrity across contexts is its
job. The exemption is structural, not a list of convenient files.

**How it reads the source** (`ast`, no import of the code under test): every
`import app.x…`, `from app.x… import …`, `from app import x` and relative
`from .. import …` under `app/`, at any depth — an import inside a function
(a «lazy» import) counts exactly like one at the top of the module.

**The debt list** (`_DEBT`) is the exact set of upward imports on `main` on
2026-11-11, keyed by (file, imported module, imported name), each with the
ticket that removes it. The test fails on an upward import that is not
listed, AND on a listed entry that no longer exists — so the list can only
shrink, and the ticket that removes an import also removes its entry.

Not seen (stated, not hidden): a module named in a string
(`importlib.import_module("app.sales.public")`, none exist today), and a name
reached through `from … import *`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

# ADR-131's layer order. Same rank = peers (neither imports the other).
_RANK: dict[str, int] = {
    "core": 0,
    "platform": 1,
    "integration": 2,
    "vehicle": 3,
    "customer": 4,
    "valuation": 5,
    "inventory": 6,
    "parts": 6,
    "sales": 7,
    "aftersales": 7,
    "finance": 8,
    "reporting": 9,
    "compliance": 9,
}

# Top-level modules of app/ and what they count as. The composition roots
# rank above every context.
_COMPOSITION_ROOT_RANK = 100
_TOP_LEVEL: dict[str, int] = {
    "db": _RANK["core"],
    "api": _COMPOSITION_ROOT_RANK,
    "main": _COMPOSITION_ROOT_RANK,
    "worker": _COMPOSITION_ROOT_RANK,
    "model_registry": _COMPOSITION_ROOT_RANK,
    "reconciliation_runner": _COMPOSITION_ROOT_RANK,
    "ownership_registry": _COMPOSITION_ROOT_RANK,
}

# The upward imports that exist today, each with the ticket that removes it
# (KAN-611's dependency strand). (file, imported module, imported name) ->
# ticket. A plain `import app.x.y` has the name "".
_DEBT: dict[tuple[str, str, str], str] = {
    # inventory -> sales
    ("app/inventory/services/purchase.py", "app.sales.public", "contract_with_invoice_requested_for_stock_item"): (
        "KAN-630"
    ),
    ("app/inventory/services/reservation_sweep.py", "app.sales.public", "ContractStatus"): "KAN-630",
    ("app/inventory/services/reservation_sweep.py", "app.sales.public", "get_contract_statuses"): "KAN-630",
    # valuation -> sales
    ("app/valuation/services/valuation.py", "app.sales.public", "valuations_carried_by_pending_contracts"): "KAN-631",
    ("app/valuation/services/valuation.py", "app.sales.public", "valuations_carried_by_signed_contracts"): "KAN-631",
    ("app/valuation/api/valuations.py", "app.sales.public", "valuations_carried_by_signed_contracts"): "KAN-631",
    # vehicle -> customer (customer is above vehicle, ADR-131)
    ("app/vehicle/api/vehicle_mdm.py", "app.customer.public", "allocate_vehicle_party"): "KAN-634",
    ("app/vehicle/api/vehicle_mdm.py", "app.customer.public", "customer_display_name"): "KAN-634",
    ("app/vehicle/api/vehicle_mdm.py", "app.customer.public", "get_customer_or_404"): "KAN-634",
    ("app/vehicle/api/vehicle_mdm_detail.py", "app.customer.public", "list_vehicle_party_holders"): "KAN-634",
    ("app/vehicle/schemas/vehicle_mdm.py", "app.customer.public", "VehiclePartyRole"): "KAN-634",
    ("app/vehicle/services/merge.py", "app.customer.public", "repoint_vehicle_party"): "KAN-634",
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(_REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(source: str, module: str, *, is_package: bool) -> Iterator[tuple[int, str, str]]:
    """Yield (line, imported module, imported name) for every import in the
    source, at any depth. `from app import sales` yields ("app.sales", "");
    a relative import is resolved against `module`."""
    tree = ast.parse(source)
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, ""
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")
                if node.level > 1:
                    base_parts = base_parts[: -(node.level - 1)]
                base = ".".join(base_parts)
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            for alias in node.names:
                if target == "app":
                    # `from app import sales` names a package, not an attribute.
                    yield node.lineno, f"app.{alias.name}", ""
                else:
                    yield node.lineno, target, alias.name


def _unit_name(top_level: str) -> str:
    # app/db.py is part of the cross-cutting layer, not a unit of its own.
    return "core" if top_level == "db" else top_level


def _rank_of_target(target: str) -> tuple[str, int] | None:
    parts = target.split(".")
    if len(parts) < 2 or parts[0] != "app":
        return None
    head = parts[1]
    if head in _RANK:
        return head, _RANK[head]
    if head in _TOP_LEVEL:
        return _unit_name(head), _TOP_LEVEL[head]
    return None


def _source_unit(path: Path) -> tuple[str, int] | None:
    rel = path.relative_to(_APP_ROOT)
    head = rel.parts[0]
    if len(rel.parts) == 1:
        head = rel.stem
        return (_unit_name(head), _TOP_LEVEL[head]) if head in _TOP_LEVEL else None
    return (head, _RANK[head]) if head in _RANK else None


def _is_exempt(path: Path) -> bool:
    rel = path.relative_to(_APP_ROOT)
    # ADR-107: app/<context>/reconciliation.py reads across by design.
    return len(rel.parts) == 2 and rel.parts[1] == "reconciliation.py" and rel.parts[0] in _RANK


def _upward_imports(path: Path, source: str) -> Iterator[tuple[int, str, str, str, str]]:
    """Yield (line, imported module, imported name, source unit, target unit)
    for each import in `path` that does not go down the layer order."""
    unit = _source_unit(path)
    if unit is None or _is_exempt(path):
        return
    src_name, src_rank = unit
    if src_rank >= _COMPOSITION_ROOT_RANK:
        return  # a composition root may import anything
    for line, target, name in _imports(source, _module_name(path), is_package=path.name == "__init__.py"):
        resolved = _rank_of_target(target)
        if resolved is None:
            continue
        tgt_name, tgt_rank = resolved
        if tgt_name == src_name:
            continue
        if tgt_rank >= src_rank:
            yield line, target, name, src_name, tgt_name


def _scan() -> dict[tuple[str, str, str], list[str]]:
    found: dict[tuple[str, str, str], list[str]] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for line, target, name, src, tgt in _upward_imports(path, path.read_text(encoding="utf-8")):
            found.setdefault((rel, target, name), []).append(f"{rel}:{line} ({src} -> {tgt})")
    return found


def test_no_upward_import_outside_the_debt_list() -> None:
    found = _scan()
    new = {key: where for key, where in found.items() if key not in _DEBT}
    assert not new, (
        "A context imports a context at its own rank or above it (ADR-131: calls go down the "
        "layer order core · platform · integration · vehicle · customer · valuation · inventory/parts · "
        "sales/aftersales · finance · reporting/compliance). Carry the fact in an event and keep a local "
        "projection, or have the higher context pass it in the call. Do not add to the debt list:\n"
        + "\n".join(f"  {w}  imports {key[2] or key[1]} from {key[1]}" for key, ws in new.items() for w in ws)
    )


def test_every_debt_entry_still_exists() -> None:
    found = _scan()
    gone = sorted(key for key in _DEBT if key not in found)
    assert not gone, (
        "These debt-list entries no longer exist — the list may only shrink: remove them "
        "(the ticket that removed the import removes its entry):\n"
        + "\n".join(f"  {f}: {n or m} from {m} ({_DEBT[(f, m, n)]})" for f, m, n in gone)
    )


def test_every_context_package_has_a_rank() -> None:
    packages = {p.name for p in _APP_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    unranked = sorted(packages - set(_RANK) - set(_TOP_LEVEL))
    assert not unranked, f"New package(s) under app/ with no place in ADR-131's layer order: {unranked}"
    modules = {p.stem for p in _APP_ROOT.glob("*.py") if p.stem != "__init__"}
    unknown = sorted(modules - set(_TOP_LEVEL))
    assert not unknown, f"New top-level module(s) under app/ that are neither core nor a composition root: {unknown}"


# --- self-tests of the reader -------------------------------------------------------------


def _upward_in(rel: str, source: str) -> list[tuple[str, str, str, str]]:
    path = _APP_ROOT / rel
    return [(t, n, s, g) for _, t, n, s, g in _upward_imports(path, source)]


def test_reader_sees_a_lazy_import_inside_a_function() -> None:
    source = "def f():\n    from app.sales.public import thing\n    return thing\n"
    assert _upward_in("platform/services/x.py", source) == [("app.sales.public", "thing", "platform", "sales")]


def test_reader_sees_plain_package_and_relative_forms() -> None:
    assert _upward_in("vehicle/x.py", "import app.inventory.public\n") == [
        ("app.inventory.public", "", "vehicle", "inventory")
    ]
    assert _upward_in("vehicle/x.py", "from app import sales\n") == [("app.sales", "", "vehicle", "sales")]
    assert _upward_in("vehicle/services/x.py", "from ...sales.public import f\n") == [
        ("app.sales.public", "f", "vehicle", "sales")
    ]


def test_reader_allows_downward_same_context_and_composition_roots() -> None:
    assert _upward_in("sales/services/x.py", "from app.vehicle.public import f\nfrom app.sales.models import M\n") == []
    assert _upward_in("sales/services/x.py", "from .y import f\nfrom app.core.errors import E\nfrom app.db import Base\n") == []
    assert _upward_in("worker.py", "from app.sales.public import f\n") == []
    assert _upward_in("api/router.py", "from app.sales.public import f\n") == []


def test_reader_refuses_peers_core_and_composition_root_targets() -> None:
    assert _upward_in("parts/x.py", "from app.inventory.public import f\n") == [
        ("app.inventory.public", "f", "parts", "inventory")
    ]
    assert _upward_in("core/x.py", "from app.platform.public import f\n") == [
        ("app.platform.public", "f", "core", "platform")
    ]
    assert _upward_in("sales/x.py", "from app.worker import run\n") == [("app.worker", "run", "sales", "worker")]


def test_reader_exempts_only_the_context_reconciliation_module() -> None:
    source = "from app.sales.public import f\n"
    assert _upward_in("vehicle/reconciliation.py", source) == []
    assert _upward_in("vehicle/services/reconciliation.py", source) == [("app.sales.public", "f", "vehicle", "sales")]
