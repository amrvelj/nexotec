"""Architecture test (KAN-90, G-93): ADR-047 — "a write that spans two
contexts is a call with a compensating action, never a shared
transaction." The import-linter cannot see this rule: a `Session` handed
across `<context>.public` creates an import edge whether or not the callee
commits, so ADR-047 itself says the rule "needs its own test in each
context that makes a cross-context call". This is that test, done once for
every context by reading the source (`ast`, no database).

What it proves:

1. Every function one context imports from another context's `public.py`
   is classified here, exactly once, as one of
   - `_OWN_COMMIT_WRITES` — a cross-context write that commits its own
     transaction (the convention the code calls "Pattern B");
   - `_SHARED_TRANSACTION_EXCEPTIONS` — a write that joins the caller's
     transaction (ADR-047's forbidden shape). One entry, the tracked debt,
     see below;
   - `_READS` — reviewed as reading only.
   A new cross-context call nobody has classified fails by name, and so
   does an entry no context calls any more. Classes, enums, exceptions and
   constants imported across a seam are not calls and are not classified.
2. Every `_OWN_COMMIT_WRITES` entry names the function whose own body
   holds its commit, and that function still calls `.commit()`; the public
   entry point reaches it through functions of its OWN context, followed
   transitively (`consume_valuation_for_contract` commits in `mark_used`,
   `call_capability` in `record_call`, `create_or_get_vehicle_mdm` in
   `create_vehicle_mdm`). Delete the commit, or the call that leads to it,
   and this fails naming the function. A commit reached only through a
   third context's public function does not count.
   A commit home may not take a `commit`/`autocommit` switch a caller could
   turn off (a switch under another name is not recognised), and a
   savepoint's `.commit()` is not a commit.
3. No `_READS` function reaches a write shape this test recognises — a
   session write or commit, a row lock, an outbox/audit/idempotency
   record, SQLAlchemy DML or raw DML text, or an assignment to an
   attribute (the ORM write idiom) — and no
   `_SHARED_TRANSACTION_EXCEPTIONS` function reaches a `.commit()`: a read
   that starts writing has to be classified again, and the tracked debt,
   once it commits on its own, must move to `_OWN_COMMIT_WRITES` (and its
   follow-up ticket be closed).
4. One context reaches another only as `from app.<ctx>.public import
   <name>` — a whole public module, another context's non-public module
   (import-linter forbids only models/services/api) or a public function
   exported by assignment (an alias, a lambda, a `partial(...)`) fails,
   since point 1 could not see the call.
   Every package under app/ is one of the twelve contexts or declared
   wiring (`core`, `api`).

The one known debt, tracked here, not ruled — decided by Anto on 2026-10-07
(KAN-90, exit criterion 1): `app.sales.public.repoint_customer_transactions`, called by
`app.customer.services.customer._repoint_transactions` inside the customer
merge, does NOT commit; its docstring says it "joins the caller's
transaction". The caller's own comment already calls this "tracked
residual coupling ... replace with a `customer.merged` event that sales
consumes idempotently". It is recorded here rather than fixed because the
table it touches is retired for new business writes (ADR-050:
`_refuse_retired_write()` refuses every create/update/complete/cancel), so
the debt is bounded to legacy rows touched by a merge; the replacement is
KAN-185.

Two more found while building KAN-90 were fixed rather than recorded
(Anto, 2026-10-07): the retired `complete_transaction`'s unreachable body,
which wrote the vehicle's custody event inside Sales' transaction, was
deleted; and `call_capability` now writes its log on a session of its own
instead of committing the caller's (a failed catalogue sync used to keep
its half-written rows).

What it does not prove: that the caller makes the call OUTSIDE its own
transaction, in the right order, with an Idempotency-Key and a
compensating action, nor which session a commit runs on — an own-commit
write sharing the caller's session commits whatever the caller has
pending. The behavioural tests cover those per call
(`test_inventory_reservation`, `test_sales_lifecycle_reservation`,
`test_sales_trade_in_valuation_use`, `test_inventory_reservation_sweep`
(KAN-122: Stock's sweep reads Sales' statuses outside its write
transactions, by recording each transaction's statements), and in `test_integration_gateway`
the two KAN-90 tests on the caller's own rows). It does not follow a
write made by assigning to another context's ORM object (rule 1's
territory), and it scans the twelve contexts only: the composition roots
directly under app/ (main, worker, reconciliation_runner) wire contexts
together. scripts/ is scanned separately, at the end of this file:
`scripts/migrate_transaction_rows.py`'s shared Sales+Inventory
transaction is the one bounded exception (ADR-091, KAN-106), and no
other script may reach two contexts' internals.

Same discipline as test_no_ambient_group_read.py: explicit names with a
reason each, never a whole-module or whole-directory exemption.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

# The twelve bounded contexts (CLAUDE.md). Every package directly under
# app/ is one of these or declared in _NOT_A_CONTEXT, so a new context
# cannot be skipped by the scan without a visible edit here.
_CONTEXTS = frozenset(
    {
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
    }
)
# app/core is cross-cutting code importing no context (CLAUDE.md rule 8);
# app/api is the router wiring that mounts every context's endpoints.
# Modules directly under app/ (main, worker, model_registry,
# reconciliation_runner, ownership_registry, db) are composition roots, not
# contexts.
_NOT_A_CONTEXT = frozenset({"core", "api"})

# A function whose job is to commit must not be able to switch that off.
_COMMIT_SWITCHES = frozenset({"commit", "autocommit"})

# What a read must never reach: session writes, row locks taken in the
# caller's transaction, and the write helpers of app/core.
_SESSION_WRITE_METHODS = frozenset({"add", "add_all", "delete", "flush", "merge", "commit", "with_for_update"})
_WRITE_HELPERS = frozenset(
    {"publish", "publish_event", "record_audit_event", "store_response", "insert", "update", "delete"}
)
_WRITE_SQL = ("insert", "update", "delete", "merge", "truncate")

# Functions a read reaches that assign attributes of plain in-process
# objects, never of a database row: their attribute assignments are not
# writes. Every other write shape is still checked in them.
_IN_PROCESS_STATE = {
    # The per-connection circuit breaker is a dataclass in process memory
    # (resilience.py's own docstring); is_circuit_open half-opens it.
    "app.integration.services.resilience.is_circuit_open",
}

# Cross-context writes that commit their own transaction (ADR-047), keyed
# by the public symbol other contexts import. Value: the function whose own
# body holds the write's commit (the public entry point must reach it
# within its own context), and who calls it. Naming the commit's home, not
# just "a commit somewhere", keeps a conditional second commit from hiding
# the loss of the one that always runs.
_OWN_COMMIT_WRITES = {
    # Stock reservation for a confirmed contract, and its release on
    # cancellation or as the confirmation's compensation (WP-7 PR-4). The
    # orphan sweep moved into inventory (KAN-122) and calls release itself.
    # Since KAN-266 release() and the HTTP endpoint's release_without_key()
    # share `_release`, which holds the commit.
    "app.inventory.public.reserve_for_contract": (
        "app.inventory.services.reservation.reserve_for_contract",
        "sales: confirm_contract",
    ),
    "app.inventory.public.release": (
        "app.inventory.services.reservation._release",
        "sales: cancel_contract, confirm_contract's compensation",
    ),
    # A vehicle merge re-points VehicleParty rows; customer stays their
    # writer (FR-V-12). Called after vehicle's own commit.
    "app.customer.public.repoint_vehicle_party": (
        "app.customer.services.customer.repoint_vehicle_party",
        "vehicle: merge flow",
    ),
    # Trade-in and the vehicle 360 allocate a customer to a vehicle
    # (FR-V-05, ADR-064).
    "app.customer.public.allocate_vehicle_party": (
        "app.customer.services.customer.allocate_vehicle_party",
        "sales: trade-in; vehicle: allocate endpoint",
    ),
    # The customer merge re-points offers and contracts (WP-8 PR-7),
    # after the merge itself has committed.
    "app.sales.public.repoint_customer_sales_records": (
        "app.sales.services.customer_merge.repoint_customer_sales_records",
        "customer: merge flow",
    ),
    # A confirmation consumes its trade-in valuation, and reverts it as
    # the compensating action (KAN-101, ADR-074).
    "app.valuation.public.consume_valuation_for_contract": (
        "app.valuation.services.valuation.mark_used",
        "sales: confirm_contract",
    ),
    "app.valuation.public.revert_valuation_use": (
        "app.valuation.services.valuation.revert_use",
        "sales: confirm_contract's compensation",
    ),
    # A VIN resolves to (or creates) the vehicle master record (FR-V-04).
    # Every caller hands it a session of its own — never the request's,
    # whose pending work its commit would land and its race guard's
    # rollback discard — and resolves a lost insert race to the winner's
    # record (FR-V-15).
    "app.vehicle.public.create_or_get_vehicle_mdm": (
        "app.vehicle.services.vehicle_mdm.create_vehicle_mdm",
        (
            "inventory: vehicle_resolution (stock create with a VIN, KAN-244; promotion), on its own session "
            "(KAN-257); sales: trade-in, on its own session (KAN-328); valuation: create, on its own session "
            "(KAN-328)"
        ),
    ),
    # The daily job composition root runs the catalogue delta per tenant.
    # Since KAN-78 the run's unconditional sync-state commit lives in
    # `_save_run_progress` (reached through `_run`), shared by the delta and
    # the full seed it can fall back to.
    "app.vehicle.public.run_daily_delta_for_tenant": (
        "app.vehicle.services.catalogue_sync._save_run_progress",
        "integration: daily_jobs",
    ),
    # Every provider call writes exactly one integration_call_log row,
    # committed in record_call on the gateway's OWN session, never the
    # caller's (KAN-90; tests/test_integration_gateway.py pins that the
    # caller's rows are left to the caller). A captured payload commits
    # again, only sometimes — which is why the commit's home is named.
    "app.integration.public.call_capability": (
        "app.integration.services.gateway.record_call",
        "inventory: marketplace transmission; vehicle: catalogue seed and daily delta",
    ),
}

# ADR-047's forbidden shape, known and tracked. Anto, 2026-10-07 (KAN-90):
# recorded, not fixed here — see the module docstring. KAN-185 replaces it.
_SHARED_TRANSACTION_EXCEPTIONS = {
    "app.sales.public.repoint_customer_transactions": (
        "customer: _repoint_transactions inside the merge transaction; legacy table retired by ADR-050; "
        "to be replaced by a customer.merged event consumed by sales (KAN-185)"
    ),
}

# Reviewed: read only, no commit reachable.
_READS = {
    "app.customer.public.customer_display_name",
    # KAN-259: builds the EXISTS Sales' nightly reconciliation runs; reads nothing itself.
    "app.customer.public.customer_in_group",
    "app.customer.public.get_customer_or_404",
    "app.customer.public.has_any_basis_for_group",
    "app.customer.public.has_usable_domicile_address",
    "app.customer.public.list_vehicle_party_holders",
    "app.integration.public.get_enabled_connection",
    "app.integration.public.get_entitlement",
    "app.integration.public.resolve_adapter",
    # KAN-42: the identification waterfall asks whether VIN decode is entitled.
    "app.integration.public.vin_decode_granted",
    "app.inventory.public.get_stock_item_pricing",
    "app.inventory.public.get_stock_items_for_vehicles",
    # KAN-243: builds the actor ReferenceChecks every context's nightly
    # reconciliation runs; reads nothing itself.
    "app.platform.public.actor_reference_checks",
    "app.platform.public.get_active_reference_value_codes",
    # KAN-200/KAN-410: who may own an open record in a Betrieb (a seller, an
    # advisor) — the user row and their memberships, a SELECT only.
    "app.platform.public.get_active_user_acting_in",
    "app.platform.public.get_dealership_or_404",
    # KAN-371: the History tab's Betrieb and user names, group-scoped; a SELECT only.
    "app.platform.public.get_group_display_labels",
    # KAN-403: users' names for the nightly advisor-label refresh; a SELECT only.
    "app.platform.public.get_user_display_labels",
    "app.platform.public.get_user_or_404",
    # KAN-410: the advisor picker — users homed in the Betrieb or members of it; a SELECT only.
    "app.platform.public.list_active_users_acting_in",
    "app.platform.public.list_dealer_manager_emails",
    "app.platform.public.list_user_dealership_ids",
    # Builds a PDF from content Sales supplies; persists nothing.
    "app.platform.public.render_document",
    # KAN-122: Stock's reservation sweep reads its contracts' statuses, on a
    # session of its own (tests/test_inventory_reservation_sweep.py).
    "app.sales.public.get_contract_statuses",
    # KAN-115: valuation asks which valuations a signed contract carries.
    "app.sales.public.valuations_carried_by_signed_contracts",
    "app.valuation.public.get_valuation_or_404",
    "app.valuation.public.list_valid_valuations_for_vehicle",
    # Reads persisted sync state only (its docstring); the daily job calls
    # it after run_daily_delta_for_tenant has committed.
    "app.vehicle.public.check_sync_age_alarm_for_tenant",
    # KAN-10: offer, stock item and valuation read the configuration they reference.
    "app.vehicle.public.get_configuration_for_host",
    "app.vehicle.public.get_vehicle_equipment",
    "app.vehicle.public.get_vehicle_mdm_or_404",
    "app.vehicle.public.get_vehicle_or_404",
    # KAN-84: vehicle labels for customer's VehicleParty, batched.
    "app.vehicle.public.get_vehicle_summaries",
    "app.vehicle.public.has_current_energy_rating",
    "app.vehicle.public.match_vehicle",
}


# --- source resolution -------------------------------------------------

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _context_of(module: str) -> str:
    return module.split(".")[1]


def _module_file(module: str, root: Path) -> Path | None:
    parts = module.split(".")
    as_file = root.parent.joinpath(*parts).with_suffix(".py")
    if as_file.is_file():
        return as_file
    as_package = root.parent.joinpath(*parts, "__init__.py")
    return as_package if as_package.is_file() else None


@cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _absolute(node: ast.ImportFrom, importer: str) -> str | None:
    if node.level == 0:
        return node.module
    base = importer.split(".")[: -node.level]
    return ".".join(base + ([node.module] if node.module else []))


def _import_map(nodes: list[ast.stmt] | ast.AST, importer: str) -> dict[str, tuple[str, str | None]]:
    """local name -> (module, attribute). attribute None: the name is a
    module itself (`from app.x import services` / `import app.x as y`)."""

    found: dict[str, tuple[str, str | None]] = {}
    walked = nodes if isinstance(nodes, list) else [nodes]
    for top in walked:
        for node in ast.walk(top):
            if isinstance(node, ast.ImportFrom):
                module = _absolute(node, importer)
                if module is None or not module.startswith("app."):
                    continue
                for alias in node.names:
                    found[alias.asname or alias.name] = (module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.") and alias.asname:
                        found[alias.asname] = (alias.name, None)
    return found


def _resolve(module: str, name: str, root: Path, depth: int = 0) -> tuple[str, ast.stmt] | None:
    """Follows re-exports to the defining module. Returns (module, node)."""

    if depth > 10:
        return None
    path = _module_file(module, root)
    if path is None:
        return None
    tree = _parse(path)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return module, node
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return module, node
    target = _import_map(tree.body, module).get(name)
    if target is not None:
        source_module, attribute = target
        if attribute is None:
            return None
        # A submodule (`from app.x import services`) resolves to nothing.
        return _resolve(source_module, attribute, root, depth + 1)
    return None


def _callee(call: ast.Call, module: str, local_imports: dict, root: Path) -> tuple[str, _FunctionNode] | None:
    """The function a call resolves to, if it is defined in `module`'s own
    context. Calls into other contexts are their own seam and are judged
    by their own classification, not followed."""

    func = call.func
    resolved: tuple[str, ast.stmt] | None = None
    if isinstance(func, ast.Name):
        target = local_imports.get(func.id)
        if target is None:
            resolved = _resolve(module, func.id, root)
        elif target[1] is not None:
            resolved = _resolve(target[0], target[1], root)
    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        target = local_imports.get(func.value.id)
        if target is not None:
            owner = target[0] if target[1] is None else f"{target[0]}.{target[1]}"
            if _module_file(owner, root) is not None:
                resolved = _resolve(owner, func.attr, root)
    if resolved is None:
        return None
    callee_module, node = resolved
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if _context_of(callee_module) != _context_of(module):
        return None
    return callee_module, node


def _savepoint_names(function: _FunctionNode) -> set[str]:
    """Names bound to a savepoint (`sp = db.begin_nested()`), whose
    `.commit()` releases the savepoint and commits nothing."""

    return {
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "begin_nested"
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _commits_directly(function: _FunctionNode) -> bool:
    """A `<session>.commit()` in the function's own body. A savepoint's
    `db.begin_nested().commit()` (a call's result, or a name bound to one)
    commits nothing."""

    savepoints = _savepoint_names(function)
    for node in ast.walk(function):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "commit"):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Call) or (isinstance(receiver, ast.Name) and receiver.id in savepoints):
            continue
        return True
    return False


def _writes_directly(module: str, function: _FunctionNode, root: Path) -> bool:
    """The write shapes a reviewed read must not contain: a session write or
    commit, a row lock (`with_for_update` as a call or a keyword), a write
    helper of app/core or SQLAlchemy's DML constructs (also through an
    import alias), raw DML text, or an assignment to an attribute — the
    ORM's own write idiom, flushed by whoever commits next."""

    path = _module_file(module, root)
    assert path is not None
    imports = {**_import_map(_parse(path).body, module), **_import_map(function, module)}
    in_process_state = f"{module}.{function.name}" in _IN_PROCESS_STATE
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and not in_process_state:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Attribute) for target in targets):
                return True
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg == "with_for_update" for keyword in node.keywords):
            return True
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _SESSION_WRITE_METHODS:
            return True
        if isinstance(func, ast.Name):
            original = imports.get(func.id, (None, func.id))[1] or func.id
            if func.id in _WRITE_HELPERS or original in _WRITE_HELPERS:
                return True
            if func.id == "text" and node.args and isinstance(node.args[0], ast.Constant):
                statement = str(node.args[0].value).lstrip().lower()
                if statement.startswith(_WRITE_SQL):
                    return True
    return False


def _commit_switch(function: _FunctionNode) -> str | None:
    arguments = function.args
    for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
        if argument.arg in _COMMIT_SWITCHES:
            return argument.arg
    return None


def _reaches(
    module: str,
    function: _FunctionNode,
    root: Path,
    stop: Callable[[str, _FunctionNode], bool],
    seen: set | None = None,
) -> bool:
    """True when `stop` holds for `function` or for any function of the
    same context it calls, followed transitively."""

    seen = set() if seen is None else seen
    key = (module, function.name, function.lineno)
    if key in seen:
        return False
    seen.add(key)
    if stop(module, function):
        return True
    path = _module_file(module, root)
    assert path is not None
    local_imports = {**_import_map(_parse(path).body, module), **_import_map(function, module)}
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            callee = _callee(node, module, local_imports, root)
            if callee is not None and _reaches(callee[0], callee[1], root, stop, seen):
                return True
    return False


def _reaches_commit(module: str, function: _FunctionNode, root: Path) -> bool:
    return _reaches(module, function, root, lambda _m, f: _commits_directly(f))


def _public_function(symbol: str, root: Path) -> tuple[str, _FunctionNode]:
    module, _, name = symbol.rpartition(".")
    resolved = _resolve(module, name, root)
    assert resolved is not None, f"{symbol} no longer exists — update the classification in this test."
    defining_module, node = resolved
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)), f"{symbol} is not a function."
    return defining_module, node


# --- the scan ----------------------------------------------------------


def _package_problems(root: Path, contexts: frozenset[str]) -> list[str]:
    return [
        f"app/{path.name}/ is neither one of the bounded contexts nor declared in _NOT_A_CONTEXT."
        for path in sorted(root.iterdir())
        if path.is_dir()
        and (path / "__init__.py").is_file()
        and path.name not in contexts
        and path.name not in _NOT_A_CONTEXT
    ]


def _cross_context_imports(root: Path, contexts: frozenset[str]) -> tuple[dict[str, set[str]], list[str]]:
    """public symbol -> importing files; plus imports the scan cannot see:
    a public module imported whole, or another context's non-public module
    (import-linter forbids only models/services/api, so a `consumers` or
    `daily_jobs` import would otherwise slip past both guards)."""

    imported: dict[str, set[str]] = {}
    unseeable: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root.parent)
        if len(relative.parts) < 3 or relative.parts[1] not in contexts:
            continue
        context = relative.parts[1]
        importer = ".".join(relative.with_suffix("").parts)
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom):
                module = _absolute(node, importer) or ""
                parts = module.split(".")
                if len(parts) < 2 or parts[0] != "app" or parts[1] == context or parts[1] not in contexts:
                    continue
                if module.endswith(".public"):
                    for alias in node.names:
                        imported.setdefault(f"{module}.{alias.name}", set()).add(str(relative))
                elif len(parts) == 2 and all(alias.name == "public" for alias in node.names):
                    unseeable.append(f"{relative}:{node.lineno} `from {module} import public`")
                else:
                    names = ", ".join(alias.name for alias in node.names)
                    unseeable.append(f"{relative}:{node.lineno} `from {module} import {names}`")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if len(parts) >= 2 and parts[0] == "app" and parts[1] != context and parts[1] in contexts:
                        unseeable.append(f"{relative}:{node.lineno} `import {alias.name}`")
    return imported, unseeable


def _cross_context_functions(root: Path, contexts: frozenset[str]) -> tuple[dict[str, set[str]], list[str]]:
    """public function -> importing files; plus public symbols the scan
    cannot classify (an assignment alias such as `x = _services.x`)."""

    imported, _ = _cross_context_imports(root, contexts)
    functions: dict[str, set[str]] = {}
    problems: list[str] = []
    for symbol, files in imported.items():
        module, _, name = symbol.rpartition(".")
        resolved = _resolve(module, name, root)
        assert resolved is not None, f"{symbol} is imported by {sorted(files)} but cannot be resolved."
        node = resolved[1]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[symbol] = files
        elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute, ast.Lambda, ast.Call)):
            problems.append(
                f"{symbol} is exported by assignment, which this test cannot follow — export it with `def` or "
                "an import in public.py."
            )
    return functions, problems


def _classification_problems(
    root: Path,
    own_commit: dict[str, tuple[str, str]],
    exceptions: dict[str, str] | set[str],
    reads: set[str],
    contexts: frozenset[str] = _CONTEXTS,
) -> list[str]:
    problems: list[str] = []
    classified = [*own_commit, *exceptions, *reads]
    for symbol in sorted({s for s in classified if classified.count(s) > 1}):
        problems.append(f"{symbol} is classified more than once.")

    calls, problems_with_exports = _cross_context_functions(root, contexts)
    problems.extend(problems_with_exports)
    for symbol in sorted(set(calls) - set(classified)):
        problems.append(
            f"{symbol} is called across a context seam by {sorted(calls[symbol])} but is not classified. "
            "Add it to _OWN_COMMIT_WRITES (it must commit its own transaction, ADR-047) or _READS."
        )
    for symbol in sorted(set(classified) - set(calls)):
        problems.append(f"{symbol} is classified but no other context calls it any more — remove the entry.")

    for symbol, (commits_in, _reason) in sorted(own_commit.items()):
        if symbol not in calls:
            continue
        module, function = _public_function(symbol, root)
        home_module, _, home_name = commits_in.rpartition(".")
        home = _resolve(home_module, home_name, root)
        if home is None or not isinstance(home[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
            problems.append(f"{symbol}: its commit's home {commits_in} no longer exists — update this test.")
            continue
        target = (home[0], home[1].name)
        switch = _commit_switch(home[1])
        if switch is not None:
            problems.append(
                f"{symbol}: {commits_in} takes a `{switch}` parameter, so a caller can switch its commit off and "
                "join the write to its own transaction (ADR-047). Classify the write by what callers pass."
            )
        elif not _commits_directly(home[1]):
            problems.append(
                f"{symbol} is a cross-context write but {commits_in} no longer commits — the write would join "
                "the caller's transaction (ADR-047)."
            )
        elif not _reaches(module, function, root, lambda m, f, target=target: (m, f.name) == target):
            problems.append(
                f"{symbol} ({module}.{function.name}) no longer reaches {commits_in}, where its commit lives — "
                "the write would join the caller's transaction (ADR-047)."
            )
    for symbol in sorted(exceptions):
        if symbol in calls:
            module, function = _public_function(symbol, root)
            if _reaches_commit(module, function, root):
                problems.append(
                    f"{symbol} now commits its own transaction — move it to _OWN_COMMIT_WRITES and close "
                    "its follow-up ticket."
                )
    for symbol in sorted(reads):
        if symbol in calls:
            module, function = _public_function(symbol, root)
            if _reaches(module, function, root, lambda m, f: _writes_directly(m, f, root)):
                problems.append(
                    f"{symbol} is classified as a read but reaches a write (session write, row lock, outbox, "
                    "audit or idempotency record) — classify it again."
                )
    return problems


def test_every_cross_context_call_is_classified_and_own_commit_writes_commit() -> None:
    problems = _classification_problems(_APP_ROOT, _OWN_COMMIT_WRITES, _SHARED_TRANSACTION_EXCEPTIONS, _READS)
    assert not problems, "ADR-047:\n" + "\n".join(problems)


def test_every_app_package_is_a_context_or_declared_wiring() -> None:
    problems = _package_problems(_APP_ROOT, _CONTEXTS)
    assert not problems, "\n".join(problems)


def test_cross_context_public_modules_are_imported_by_name() -> None:
    _, unseeable = _cross_context_imports(_APP_ROOT, _CONTEXTS)
    assert not unseeable, (
        "Import another context's public functions by name (`from app.<ctx>.public import f`) so the "
        "ADR-047 classification can see each call:\n" + "\n".join(unseeable)
    )


def test_the_known_shared_transaction_is_still_the_only_one() -> None:
    """Exit criterion 3: the debt is named, not silently exempt."""

    assert set(_SHARED_TRANSACTION_EXCEPTIONS) == {"app.sales.public.repoint_customer_transactions"}


# --- self-tests: the guard catches what it claims to catch --------------


def _write(root: Path, files: dict[str, str]) -> Path:
    app = root / "app"
    for relative, source in files.items():
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return app


_FIXTURE = {
    "stock/__init__.py": "",
    "stock/public.py": "from app.stock.services import reserve, peek, relay\n",
    "stock/services.py": (
        "def reserve(db):\n    db.add(1)\n    db.commit()\n\n"
        "def _save(db):\n    db.commit()\n\n"
        "def _capture(db):\n    if db.capture:\n        db.commit()\n\n"
        "def relay(db):\n    db.add(1)\n    _save(db)\n    _capture(db)\n\n"
        "def peek(db):\n    return db.get(1)\n"
    ),
    "sales/__init__.py": "",
    "sales/contract.py": (
        "from app.stock.public import reserve, peek, relay\n\n"
        "def confirm(db):\n    reserve(db)\n    relay(db)\n    return peek(db)\n"
    ),
}
_FIXTURE_WRITES = {
    "app.stock.public.reserve": ("app.stock.services.reserve", "sales: confirm"),
    "app.stock.public.relay": ("app.stock.services._save", "sales: confirm"),
}
_FIXTURE_READS = {"app.stock.public.peek"}
_FIXTURE_CONTEXTS = frozenset({"stock", "sales", "ledger"})


def _fixture_problems(app: Path, writes: dict, exceptions: dict | set, reads: set) -> list[str]:
    return _classification_problems(app, writes, exceptions, reads, _FIXTURE_CONTEXTS)


def _replace(source: str, old: str, new: str) -> str:
    assert old in source, old
    return source.replace(old, new, 1)


def test_self_test_a_well_formed_tree_passes(tmp_path: Path) -> None:
    app = _write(tmp_path, _FIXTURE)
    assert _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS) == []


def test_self_test_a_write_with_its_commit_removed_fails(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"], "    db.add(1)\n    db.commit()\n", "    db.add(1)\n"
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.reserve" in problems[0] and "no longer commits" in problems[0]


def test_self_test_a_conditional_commit_elsewhere_does_not_hide_the_lost_one(tmp_path: Path) -> None:
    """The shape call_capability has: record_call always commits, a
    captured payload sometimes commits again. Losing the first must fail
    even though the second is still reachable."""

    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"], "def _save(db):\n    db.commit()", "def _save(db):\n    db.flush()"
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.relay" in problems[0] and "_save no longer commits" in problems[0]


def test_self_test_a_write_that_stops_calling_its_commit_home_fails(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(files["stock/services.py"], "    _save(db)\n", "")
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.relay" in problems[0] and "no longer reaches" in problems[0]


def test_self_test_an_unclassified_cross_context_call_fails(tmp_path: Path) -> None:
    app = _write(tmp_path, _FIXTURE)
    writes = {"app.stock.public.reserve": _FIXTURE_WRITES["app.stock.public.reserve"]}
    problems = _fixture_problems(app, writes, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.relay" in problems[0] and "not classified" in problems[0]


def test_self_test_a_stale_entry_fails(tmp_path: Path) -> None:
    app = _write(tmp_path, _FIXTURE)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS | {"app.stock.public.gone"})
    assert len(problems) == 1 and "app.stock.public.gone" in problems[0] and "no other context calls it" in problems[0]


def test_self_test_a_read_that_commits_fails(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] += "\ndef sneaky(db):\n    db.commit()\n"
    files["stock/public.py"] = "from app.stock.services import reserve, peek, relay, sneaky\n"
    files["sales/contract.py"] = _replace(files["sales/contract.py"], "peek, relay", "peek, relay, sneaky")
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS | {"app.stock.public.sneaky"})
    assert len(problems) == 1 and "sneaky" in problems[0] and "read" in problems[0]


def test_self_test_a_shared_transaction_exception_that_starts_committing_fails(tmp_path: Path) -> None:
    app = _write(tmp_path, _FIXTURE)
    writes = {"app.stock.public.relay": _FIXTURE_WRITES["app.stock.public.relay"]}
    problems = _fixture_problems(app, writes, {"app.stock.public.reserve"}, _FIXTURE_READS)
    assert len(problems) == 1 and "move it to _OWN_COMMIT_WRITES" in problems[0]


def test_self_test_a_commit_home_in_another_context_is_not_followed(tmp_path: Path) -> None:
    """A write whose only commit is a third context's public function does
    not commit its own transaction — the third context's commit is judged
    at its own seam."""

    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "def _save(db):\n    db.commit()",
        "from app.ledger.public import book\n\ndef _save(db):\n    book(db)",
    )
    files["ledger/__init__.py"] = ""
    files["ledger/public.py"] = "def book(db):\n    db.commit()\n"
    app = _write(tmp_path, files)
    writes = {**_FIXTURE_WRITES, "app.stock.public.relay": ("app.ledger.public.book", "sales: confirm")}
    problems = _fixture_problems(app, writes, set(), _FIXTURE_READS)
    # stock now imports ledger.public too, so `book` needs a classification
    # of its own; relay must still be reported as not reaching its commit.
    assert any("app.stock.public.relay" in p and "no longer reaches" in p for p in problems), problems


def test_self_test_a_module_level_public_import_is_reported(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["sales/other.py"] = "from app.stock import public\n"
    app = _write(tmp_path, files)
    _, unseeable = _cross_context_imports(app, _FIXTURE_CONTEXTS)
    assert unseeable == ["app/sales/other.py:1 `from app.stock import public`"]


def test_self_test_a_non_public_cross_context_import_is_reported(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["sales/other.py"] = "from app.stock.services import reserve\n"
    app = _write(tmp_path, files)
    _, unseeable = _cross_context_imports(app, _FIXTURE_CONTEXTS)
    assert unseeable == ["app/sales/other.py:1 `from app.stock.services import reserve`"]


def test_self_test_a_read_that_writes_without_committing_fails(tmp_path: Path) -> None:
    """The shape ADR-047 forbids: a "read" whose write joins the caller's
    transaction."""

    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "def peek(db):\n    return db.get(1)",
        "def peek(db):\n    db.add(2)\n    return db.get(1)",
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.peek" in problems[0] and "reaches a write" in problems[0]


def test_self_test_a_commit_home_with_a_commit_switch_fails(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "def reserve(db):\n    db.add(1)\n    db.commit()",
        "def reserve(db, commit=True):\n    db.add(1)\n    if commit:\n        db.commit()",
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.reserve" in problems[0] and "`commit` parameter" in problems[0]


def test_self_test_a_savepoint_commit_is_not_a_commit(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "    db.add(1)\n    db.commit()\n",
        "    db.add(1)\n    db.begin_nested().commit()\n",
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.reserve" in problems[0] and "no longer commits" in problems[0]


def test_self_test_an_assignment_alias_export_is_reported(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/public.py"] += "from app.stock import services as _services\nhidden = _services.reserve\n"
    files["sales/contract.py"] = _replace(files["sales/contract.py"], "peek, relay", "peek, relay, hidden")
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.hidden" in problems[0] and "by assignment" in problems[0]


def test_self_test_an_undeclared_app_package_is_reported(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["newctx/__init__.py"] = ""
    app = _write(tmp_path, files)
    assert _package_problems(app, _FIXTURE_CONTEXTS) == [
        "app/newctx/ is neither one of the bounded contexts nor declared in _NOT_A_CONTEXT."
    ]


@pytest.mark.parametrize(
    "body",
    [
        "    row = db.get(1)\n    row.price = 0\n    return row",
        '    return db.execute(text("UPDATE stock SET price = 0"))',
        "    row = db.get(1)\n    db.refresh(row, with_for_update=True)\n    return row",
        "    emit(db, 1)\n    return db.get(1)",
        "    return db.execute(delete(1))",
    ],
    ids=["attribute-assignment", "raw-dml-text", "refresh-row-lock", "aliased-outbox-publish", "sqlalchemy-delete"],
)
def test_self_test_a_read_with_another_write_shape_fails(tmp_path: Path, body: str) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "def peek(db):\n    return db.get(1)",
        f"from app.core.outbox import publish as emit\n\ndef peek(db):\n{body}",
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.peek" in problems[0] and "reaches a write" in problems[0]


def test_self_test_a_savepoint_bound_to_a_name_is_not_a_commit(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/services.py"] = _replace(
        files["stock/services.py"],
        "    db.add(1)\n    db.commit()\n",
        "    savepoint = db.begin_nested()\n    db.add(1)\n    savepoint.commit()\n",
    )
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.reserve" in problems[0] and "no longer commits" in problems[0]


def test_self_test_a_partial_export_is_reported(tmp_path: Path) -> None:
    files = dict(_FIXTURE)
    files["stock/public.py"] += (
        "from functools import partial\nfrom app.stock.services import reserve as _reserve\n"
        "hidden = partial(_reserve)\n"
    )
    files["sales/contract.py"] = _replace(files["sales/contract.py"], "peek, relay", "peek, relay, hidden")
    app = _write(tmp_path, files)
    problems = _fixture_problems(app, _FIXTURE_WRITES, set(), _FIXTURE_READS)
    assert len(problems) == 1 and "app.stock.public.hidden" in problems[0] and "by assignment" in problems[0]


# --- scripts/ (KAN-106, ADR-091) ----------------------------------------
#
# The import-linter's root package is `app` and the scan above walks the
# twelve contexts, so a script under scripts/ that reaches into two
# contexts' internals is seen by neither. One does, by ruling (Anto,
# 2026-10-12, option A of KAN-106; ADR-091 in the Target Architecture):
# scripts/migrate_transaction_rows.py — the one-time, no-outbox historical
# backfill of the legacy `transaction` table — may write Sales (sales_offer,
# sales_contract, sales_stock_item_purchase) and Inventory (stock_item) in
# one transaction, import `app.inventory.models.stock_item` and
# `app.inventory.services.stock_item.allocate_stock_number` directly, read
# the frozen legacy `vehicle` row directly, and read the customer, dealership
# and vehicle_mdm rows it resolves against through their models, because its
# dry run is the real run rolled back (KAN-263) and a half-written history would have to
# be undone by hand. The exception names exactly that script, publishes no
# events, and expires once the script has run in production, when the
# script is deleted. Nothing else may cite it. These tests are the
# exception in executable form — the same discipline as above: exact
# names, a reason each, never a whole-directory exemption.

_SCRIPTS_ROOT = _REPO_ROOT / "scripts"
_ADR_091_SCRIPT = "migrate_transaction_rows.py"
# Either marker in a script's module docstring is a citation of the exception.
_ADR_091_CITATIONS = ("ADR-091", "ADR-047 EXCEPTION")
# A context's internals in the import-linter's sense (models/services/api);
# a script has no `api` to import, so these two are what the scan looks for.
_CONTEXT_INTERNALS = frozenset({"models", "services"})


@dataclass(frozen=True)
class _ScriptReach:
    """What a script that reaches several contexts' internals imports, and
    whether it commits — pinned exactly, so a widening is a visible edit."""

    modules: frozenset[str]
    commits: bool
    reason: str


# The exact internal modules the excepted script imports: the Inventory
# internals, the legacy vehicle and the three resolution reads are what
# ADR-091 names (its second, third and fourth items); the Sales imports are
# recorded too, so the pinned shape is the whole shape, not a selection of
# it.
_ADR_091_IMPORTS = frozenset(
    {
        # ADR-091: Inventory's internals, used instead of inventory.public —
        # LifecycleStatus, StockItem, StockItemCondition and allocate_stock_number.
        "app.inventory.models.stock_item",
        "app.inventory.services.stock_item",
        # ADR-091: the frozen legacy `vehicle` row (LegacyVehicle, its condition),
        # read with db.get rather than through vehicle.public.
        "app.vehicle.models.vehicle",
        # Sales' own rows and computations: the table the script migrates and the
        # production code it reuses (ADR-050; the migrations rule asks for that).
        "app.sales.models.contract",
        "app.sales.models.offer",
        "app.sales.models.transaction",
        "app.sales.services.numbering",
        "app.sales.services.pricing",
        "app.sales.services.snapshot",
        "app.sales.services.stock_item_purchase",
        # Resolution reads made through the models rather than through
        # `<context>.public` (the customer, the dealership and its vat_rate, the
        # vehicle_mdm row a legacy vehicle migrated to): ADR-091's fourth item,
        # named there and in the script's docstring since the KAN-106 review.
        "app.customer.models.customer",
        "app.platform.models.dealership",
        "app.vehicle.models.vehicle_mdm",
    }
)

# Every script that imports the internals of two or more contexts, with
# exactly what it imports and what it does with it. Only the first is under
# ADR-091. The other three were found by KAN-106's scan and are named here
# as they are, not excepted: none has a ruling yet, and the report asks for
# one. A new script that reaches two contexts fails by name, and so does a
# recorded one whose imports change.
_SCRIPTS_REACHING_SEVERAL_CONTEXTS: dict[str, _ScriptReach] = {
    _ADR_091_SCRIPT: _ScriptReach(
        _ADR_091_IMPORTS,
        commits=True,
        reason="ADR-091 (KAN-106): the one bounded exception — see the section comment and the script's docstring",
    ),
    "repoint_legacy_vehicle_party_references.py": _ScriptReach(
        frozenset({"app.customer.models.vehicle_party", "app.vehicle.models.vehicle_mdm"}),
        commits=True,
        reason=(
            "writes one context (customer: VehicleParty.vehicle_id, in place, its own commit) and reads one "
            "(vehicle: vehicle_mdm, to resolve the replacement id) — one writer, so not ADR-047's two-context "
            "write; the read goes through the model rather than vehicle.public. KAN-31 safety net, expected to "
            "report nothing. Not under ADR-091; found by KAN-106, no ruling yet"
        ),
    ),
    "seed_migration_smoke_test.py": _ScriptReach(
        frozenset(
            {
                "app.customer.models.customer",
                "app.customer.models.vehicle_party",
                "app.platform.models.dealership",
                "app.sales.models.transaction",
                "app.vehicle.models.catalogue",
                "app.vehicle.models.vehicle",
            }
        ),
        commits=True,
        reason=(
            "CI only: inserts one consistent row per table into customer, platform, sales and vehicle in one "
            "commit, through the ORM and not the services on purpose (its docstring), so the migration round "
            "trip runs over data. A test fixture, never run against a real database. Not under ADR-091; found "
            "by KAN-106, no ruling yet"
        ),
    ),
    "verify_migration_smoke_test_seed.py": _ScriptReach(
        frozenset(
            {
                "app.customer.models.customer",
                "app.customer.models.vehicle_party",
                "app.platform.models.dealership",
                "app.platform.models.user",
                "app.sales.models.transaction",
                "app.vehicle.models.catalogue",
                "app.vehicle.models.provider",
                "app.vehicle.models.vehicle",
            }
        ),
        commits=False,
        reason=(
            "CI only, reads only (no commit): checks the rows seed_migration_smoke_test.py wrote survived the "
            "upgrade, by their fixed values. Not under ADR-091; found by KAN-106, no ruling yet"
        ),
    ),
}


def _script_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _script_docstring(path: Path) -> str:
    return ast.get_docstring(_parse(path)) or ""


def _scripts_citing_adr_091(root: Path) -> list[str]:
    return [
        str(path.relative_to(root))
        for path in _script_files(root)
        if any(marker in _script_docstring(path) for marker in _ADR_091_CITATIONS)
    ]


def _context_internal_imports(path: Path, contexts: frozenset[str]) -> frozenset[str]:
    """The `app.<context>.<models|services>.<module>` modules a file imports,
    at module level or inside a function, by any of the three spellings
    (`from app.x.models.y import Z`, `from app.x.models import y`,
    `import app.x.models.y`)."""

    found: set[str] = set()

    def record(module: str) -> None:
        parts = module.split(".")
        if len(parts) >= 4 and parts[0] == "app" and parts[1] in contexts and parts[2] in _CONTEXT_INTERNALS:
            found.add(".".join(parts[:4]))

    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            parts = node.module.split(".")
            if len(parts) == 3 and parts[0] == "app" and parts[2] in _CONTEXT_INTERNALS:
                for alias in node.names:
                    record(f"{node.module}.{alias.name}")
            else:
                record(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name)
    return frozenset(found)


def _script_commits(path: Path) -> bool:
    """A `<session>.commit()` anywhere in the file, savepoints aside."""

    tree = _parse(path)
    savepoints = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "begin_nested"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "commit"):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Call) or (isinstance(receiver, ast.Name) and receiver.id in savepoints):
            continue
        return True
    return False


def _scripts_reaching_several_contexts(root: Path, contexts: frozenset[str]) -> dict[str, tuple[frozenset[str], bool]]:
    reaching: dict[str, tuple[frozenset[str], bool]] = {}
    for path in _script_files(root):
        modules = _context_internal_imports(path, contexts)
        if len({module.split(".")[1] for module in modules}) >= 2:
            reaching[str(path.relative_to(root))] = (modules, _script_commits(path))
    return reaching


def _script_reach_problems(root: Path, recorded: dict[str, _ScriptReach], contexts: frozenset[str]) -> list[str]:
    problems: list[str] = []
    found = _scripts_reaching_several_contexts(root, contexts)
    for script in sorted(set(found) - set(recorded)):
        modules, _ = found[script]
        problems.append(
            f"scripts/{script} imports the internals of several contexts ({', '.join(sorted(modules))}) and is "
            "not recorded in _SCRIPTS_REACHING_SEVERAL_CONTEXTS. A script writes one context and reaches the "
            "others through `<context>.public` (ADR-047, CLAUDE.md rules 3 and 12); only "
            f"scripts/{_ADR_091_SCRIPT} is excepted (ADR-091), and nothing else may cite it."
        )
    for script in sorted(set(recorded) - set(found)):
        problems.append(
            f"scripts/{script} is recorded in _SCRIPTS_REACHING_SEVERAL_CONTEXTS but no longer imports the "
            "internals of several contexts — remove the entry"
            + (" (and ADR-091 with it, once the script has run in production)." if script == _ADR_091_SCRIPT else ".")
        )
    for script in sorted(set(recorded) & set(found)):
        modules, commits = found[script]
        entry = recorded[script]
        if modules != entry.modules:
            added = ", ".join(sorted(modules - entry.modules)) or "none"
            gone = ", ".join(sorted(entry.modules - modules)) or "none"
            problems.append(
                f"scripts/{script} no longer imports exactly what _SCRIPTS_REACHING_SEVERAL_CONTEXTS records "
                f"(added: {added}; gone: {gone}). A changed reach is a visible edit here, with its reason"
                + (" — and ADR-091 names the imports it excepts." if script == _ADR_091_SCRIPT else ".")
            )
        if commits != entry.commits:
            problems.append(
                f"scripts/{script} {'now' if commits else 'no longer'} commits, but "
                f"_SCRIPTS_REACHING_SEVERAL_CONTEXTS records it as {'not committing' if commits else 'committing'} "
                "— classify it again."
            )
    return problems


def test_adr_091_is_cited_by_the_transaction_migration_and_by_nothing_else() -> None:
    """KAN-106, exit criterion 4: the exception is bounded to one script.
    The script's docstring carries the paragraph "ADR-047 EXCEPTION
    (ADR-091, KAN-106)"; no other script under scripts/ cites either."""

    assert _scripts_citing_adr_091(_SCRIPTS_ROOT) == [_ADR_091_SCRIPT]


def test_the_transaction_migration_commits_once_in_run_rows() -> None:
    """ADR-091's shape: every row's Sales and Inventory rows in ONE
    transaction, committed once at the end of `_run_rows` (a dry run rolls
    it back, KAN-263). A second commit, or one elsewhere, is a different
    shape from the one the ADR excepts."""

    tree = _parse(_SCRIPTS_ROOT / _ADR_091_SCRIPT)
    commits = [
        (function.name, node.lineno)
        for function in tree.body
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
        and not isinstance(node.func.value, ast.Call)
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id in _savepoint_names(function))
    ]
    assert [name for name, _ in commits] == ["_run_rows"], commits


def test_no_other_script_reaches_the_internals_of_several_contexts() -> None:
    """KAN-106: every script importing two or more contexts' `models` or
    `services` is named here with exactly what it imports; the excepted one
    imports exactly what ADR-091 (and this test) names."""

    problems = _script_reach_problems(_SCRIPTS_ROOT, _SCRIPTS_REACHING_SEVERAL_CONTEXTS, _CONTEXTS)
    assert not problems, "ADR-047 / ADR-091 in scripts/:\n" + "\n".join(problems)


# --- self-tests for the scripts/ guard -----------------------------------

_SCRIPT_FIXTURE_CONTEXTS = frozenset({"stock", "sales"})
_SCRIPT_FIXTURE = {
    "one.py": (
        '"""A backfill. ADR-047 EXCEPTION (ADR-091, KAN-106): the one."""\n'
        "from app.stock.models.item import Item\n"
        "from app.sales.services import numbering\n\n"
        "def run(db):\n    db.add(Item())\n    db.commit()\n"
    ),
    "two.py": '"""Reads only."""\nfrom app.sales.models.contract import Contract\n\ndef run(db):\n    return db.get(Contract, 1)\n',
}
_SCRIPT_FIXTURE_RECORDED = {
    "one.py": _ScriptReach(
        frozenset({"app.stock.models.item", "app.sales.services.numbering"}), commits=True, reason="fixture"
    ),
}


def _write_scripts(root: Path, files: dict[str, str]) -> Path:
    scripts = root / "scripts"
    scripts.mkdir()
    for name, source in files.items():
        (scripts / name).write_text(source)
    return scripts


def test_self_test_a_well_formed_scripts_tree_passes(tmp_path: Path) -> None:
    scripts = _write_scripts(tmp_path, _SCRIPT_FIXTURE)
    assert _scripts_citing_adr_091(scripts) == ["one.py"]
    assert _script_reach_problems(scripts, _SCRIPT_FIXTURE_RECORDED, _SCRIPT_FIXTURE_CONTEXTS) == []


def test_self_test_a_second_script_citing_the_exception_is_seen(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["two.py"] = _replace(files["two.py"], '"""Reads only."""', '"""Reads only, under ADR-091 too."""')
    scripts = _write_scripts(tmp_path, files)
    assert _scripts_citing_adr_091(scripts) == ["one.py", "two.py"]


def test_self_test_a_citation_outside_the_docstring_is_not_a_citation(tmp_path: Path) -> None:
    """A comment or a string in the body is not the module docstring; the
    exception is cited where a reader starts, or not at all."""

    files = dict(_SCRIPT_FIXTURE)
    files["two.py"] += "# ADR-091\nNOTE = 'ADR-047 EXCEPTION'\n"
    scripts = _write_scripts(tmp_path, files)
    assert _scripts_citing_adr_091(scripts) == ["one.py"]


def test_self_test_an_unrecorded_script_reaching_two_contexts_fails(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["two.py"] = _replace(
        files["two.py"], "from app.sales.models.contract import Contract\n",
        "from app.sales.models.contract import Contract\nimport app.stock.services.item\n",
    )
    scripts = _write_scripts(tmp_path, files)
    problems = _script_reach_problems(scripts, _SCRIPT_FIXTURE_RECORDED, _SCRIPT_FIXTURE_CONTEXTS)
    assert len(problems) == 1 and "scripts/two.py" in problems[0] and "not recorded" in problems[0], problems


def test_self_test_a_recorded_script_with_a_widened_reach_fails(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["one.py"] += "from app.stock.services import pricing\n"
    scripts = _write_scripts(tmp_path, files)
    problems = _script_reach_problems(scripts, _SCRIPT_FIXTURE_RECORDED, _SCRIPT_FIXTURE_CONTEXTS)
    assert len(problems) == 1 and "added: app.stock.services.pricing" in problems[0], problems


def test_self_test_a_recorded_script_that_stops_reaching_two_contexts_fails(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["one.py"] = _replace(files["one.py"], "from app.sales.services import numbering\n", "")
    scripts = _write_scripts(tmp_path, files)
    problems = _script_reach_problems(scripts, _SCRIPT_FIXTURE_RECORDED, _SCRIPT_FIXTURE_CONTEXTS)
    assert len(problems) == 1 and "no longer imports the internals of several contexts" in problems[0], problems


def test_self_test_a_recorded_script_whose_commit_changes_fails(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["one.py"] = _replace(files["one.py"], "    db.commit()\n", "    db.flush()\n")
    scripts = _write_scripts(tmp_path, files)
    problems = _script_reach_problems(scripts, _SCRIPT_FIXTURE_RECORDED, _SCRIPT_FIXTURE_CONTEXTS)
    assert len(problems) == 1 and "no longer commits" in problems[0], problems


def test_self_test_a_savepoint_commit_in_a_script_is_not_a_commit(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["one.py"] = _replace(files["one.py"], "    db.commit()\n", "    db.begin_nested().commit()\n")
    scripts = _write_scripts(tmp_path, files)
    assert not _script_commits(scripts / "one.py")


def test_self_test_a_function_level_import_is_seen(tmp_path: Path) -> None:
    files = dict(_SCRIPT_FIXTURE)
    files["two.py"] = _replace(
        files["two.py"], "def run(db):\n", "def run(db):\n    from app.stock.models import item\n"
    )
    scripts = _write_scripts(tmp_path, files)
    assert _context_internal_imports(scripts / "two.py", _SCRIPT_FIXTURE_CONTEXTS) == {
        "app.sales.models.contract",
        "app.stock.models.item",
    }
