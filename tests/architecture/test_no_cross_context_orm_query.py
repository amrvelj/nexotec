"""Architecture test (KAN-121, CLAUDE.md rule 2, Target Architecture P-2):
no context runs SQL on another context's tables.

import-linter (rule 3) lets a context import what another context's
`public` module exports, and today those modules still export ORM classes
(`app.platform.public.Dealership`, `app.customer.public.VehicleParty`, …;
KAN-123 is the ticket that stops it). Nothing stopped a context from
putting such a class into its own query — `select(StockItem, Dealership)
.join(Dealership, …)` — which is the cross-context join rule 2 forbids and
which breaks at runtime the day the other context changes its schema or is
extracted (ADR-015). test_no_cross_context_mapping.py (KAN-84) closes the
FK and relationship() side; this closes the query side.

How it reads the source (`ast`, no database): for every module under
`app/<context>/`, each name imported from another context's package is
resolved to the object it names; a mapped class (a subclass of
`app.db.Base`) owned by another context is a *foreign model*. The module
then fails if a foreign model, or an attribute of one (`Dealership.id`),
appears anywhere inside the arguments of

- a statement builder: `select(`, `insert(`, `update(`, `delete(`,
  `exists(`, `aliased(`, `union(`, `union_all(`;
- a query method: `.join(`, `.outerjoin(`, `.join_from(`, `.select_from(`,
  `.query(`, `.get(`, `.where(`, `.filter(`, `.filter_by(`, `.having(`,
  `.order_by(`, `.group_by(`, `.execute(`, `.scalar(`, `.scalars(`;

or if the foreign model is called (`Dealership(...)` builds another
context's row: a write).

**The one ruled exception (Anto, 2026-11-01, KAN-121 option B; ADR-107):**
a context's nightly reconciliation module, `app/<context>/reconciliation.py`,
may READ other contexts' tables — checking integrity across contexts is its
job (rule 10), and it is rewritten against `public` functions or projections
only when a context is extracted. It is an exception for reads alone: an
`insert(`/`update(`/`delete(` or a constructor call on a foreign model is
refused there too. The exemption is structural (the module's path), not a
list of convenient files, and it covers nothing else in the package.

Not seen by this gate (stated, not hidden): a foreign model handed to a
helper by keyword (`ReferenceCheck(target_model=Dealership)` in a
reconciliation module — exempt anyway), a query built from a string
(`text("SELECT … FROM dealership")`), and a foreign model reached through
a variable that is not its imported name.
"""

import ast
import importlib
from pathlib import Path

import app.model_registry  # noqa: F401  (registers every mapped class on Base)
from app.db import Base

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

# Packages under app/ that are not bounded contexts (wiring and the
# cross-cutting package, which may not import a context at all).
_NOT_A_CONTEXT = frozenset({"core", "api"})

_STATEMENT_BUILDERS = frozenset({"select", "insert", "update", "delete", "exists", "aliased", "union", "union_all"})
_WRITE_BUILDERS = frozenset({"insert", "update", "delete"})
_SQLALCHEMY_MODULE_NAMES = frozenset({"sa", "sqlalchemy"})
_QUERY_METHODS = frozenset(
    {
        "join",
        "outerjoin",
        "join_from",
        "select_from",
        "query",
        "get",
        "where",
        "filter",
        "filter_by",
        "having",
        "order_by",
        "group_by",
        "execute",
        "scalar",
        "scalars",
    }
)

# Repo-relative path -> reason. Empty for application code (KAN-121 exit
# criterion 3); the reconciliation exception above is structural, not an
# entry here. A new entry needs an ADR, not a convenience.
_ALLOWED_FILES: dict[str, str] = {}


def _context_files() -> list[tuple[str, Path]]:
    files = []
    for package in sorted(p for p in _APP_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        if package.name in _NOT_A_CONTEXT:
            continue
        for path in sorted(package.rglob("*.py")):
            files.append((package.name, path))
    return files


def _is_reconciliation_module(context: str, path: Path) -> bool:
    return path == _APP_ROOT / context / "reconciliation.py"


def _foreign_models(tree: ast.Module, context: str) -> dict[str, type]:
    """Local name -> mapped class, for every name this module imports from
    another context's package that resolves to a mapped model."""

    found: dict[str, type] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None or node.level:
            continue
        parts = node.module.split(".")
        if parts[0] != "app" or len(parts) < 2 or parts[1] in _NOT_A_CONTEXT or parts[1] == context:
            continue
        module = importlib.import_module(node.module)
        for alias in node.names:
            obj = getattr(module, alias.name, None)
            if isinstance(obj, type) and issubclass(obj, Base) and obj.__module__.split(".")[1] != context:
                found[alias.asname or alias.name] = obj
    return found


def _names_in(node: ast.AST, foreign: dict[str, type]) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in foreign}


def _call_kind(call: ast.Call) -> tuple[str, str] | None:
    """('builder'|'method', name) for a call this gate inspects, else None."""

    if isinstance(call.func, ast.Name) and call.func.id in _STATEMENT_BUILDERS:
        return "builder", call.func.id
    if isinstance(call.func, ast.Attribute):
        if call.func.attr in _QUERY_METHODS:
            return "method", call.func.attr
        # sqlalchemy.select(...) / sa.update(...) — the module spelled out, so a
        # dict's own .update(...) is never read as a statement.
        if (
            call.func.attr in _STATEMENT_BUILDERS
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in _SQLALCHEMY_MODULE_NAMES
        ):
            return "builder", call.func.attr
    return None


def _violations_in_source(source: str, *, context: str, foreign: dict[str, type], reconciliation: bool) -> list[str]:
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in foreign:
            problems.append(f"line {node.lineno}: builds another context's row: {node.func.id}(...)")
            continue
        kind = _call_kind(node)
        if kind is None:
            continue
        _, name = kind
        used = set()
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            used |= _names_in(arg, foreign)
        if not used:
            continue
        if reconciliation and name not in _WRITE_BUILDERS:
            continue
        owners = ", ".join(f"{n} ({foreign[n].__module__.split('.')[1]})" for n in sorted(used))
        problems.append(f"line {node.lineno}: {name}(...) on {owners}")
    return problems


def _scan() -> list[str]:
    problems: list[str] = []
    for context, path in _context_files():
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _ALLOWED_FILES:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        foreign = _foreign_models(tree, context)
        if not foreign:
            continue
        for problem in _violations_in_source(
            source, context=context, foreign=foreign, reconciliation=_is_reconciliation_module(context, path)
        ):
            problems.append(f"{rel}: {problem}")
    return problems


def test_no_context_queries_another_contexts_tables():
    problems = _scan()
    assert not problems, (
        "A context runs SQL on another context's table (CLAUDE.md rule 2). Ask the owning context "
        "through a function on its `public` module that returns plain data instead "
        "(KAN-121; the nightly reconciliation module is the one ruled exception, reads only, ADR-107):\n"
        + "\n".join(problems)
    )


def test_allowlisted_files_still_exist():
    missing = [p for p in _ALLOWED_FILES if not (_REPO_ROOT / p).exists()]
    assert not missing, f"Allowlisted file(s) no longer exist, update this test: {missing}"


def test_every_context_has_its_reconciliation_module_where_expected():
    """The exemption is keyed to `app/<context>/reconciliation.py`; if the
    modules moved, the exemption would silently cover nothing (harmless) or
    the moved code would fail (loud). This pins where they are today."""

    present = sorted(p.parent.name for p in _APP_ROOT.glob("*/reconciliation.py") if p.parent.name not in _NOT_A_CONTEXT)
    assert present == ["customer", "integration", "inventory", "platform", "sales", "valuation", "vehicle"]


# --- anti-vacuous: the gate must be able to fail ----------------------------------

class _Fake:
    """Stands in for a foreign mapped class in the fixtures below."""


_FOREIGN = {"Dealership": _Fake, "VehicleParty": _Fake}
_FOREIGN_OWNER = type("Owner", (), {"__module__": "app.platform.models.dealership"})


def _fixture_problems(source: str, *, reconciliation: bool = False) -> list[str]:
    foreign = {name: _FOREIGN_OWNER for name in _FOREIGN}
    return _violations_in_source(source, context="inventory", foreign=foreign, reconciliation=reconciliation)


_CROSSINGS = [
    "stmt = select(StockItem, Dealership).join(Dealership, Dealership.id == StockItem.tenant_id)",
    "group = db.get(DealerGroup, gid) or db.get(Dealership, did)",
    "ids = select(Dealership.id).where(Dealership.dealer_group_id == gid)",
    "stmt = select(StockItem).where(StockItem.tenant_id.in_(select(Dealership.id)))",
    "n = db.scalar(select(func.count()).select_from(VehicleParty))",
    "rows = db.query(Dealership).filter(Dealership.status == 'active').all()",
    "q = select(StockItem).order_by(Dealership.legal_name)",
    "db.execute(update(Dealership).values(legal_name='x'))",
    "db.add(Dealership(legal_name='x'))",
    "q = sa.select(Dealership.id)",
]

_NOT_CROSSINGS = [
    "stmt = select(StockItem).where(StockItem.tenant_id.in_(ids))",
    "def f(rows: list[tuple[StockItem, Dealership]]) -> Dealership: ...",
    "assert isinstance(d, Dealership)",
    "groups.update((d.id, d.dealer_group_id) for d in Dealership.rows)",
    "label = dealership.legal_name",
]


def test_each_crossing_shape_is_caught():
    for line in _CROSSINGS:
        assert _fixture_problems(line), f"the gate did not catch: {line!r}"


def test_ordinary_code_is_not_flagged():
    for line in _NOT_CROSSINGS:
        assert not _fixture_problems(line), f"the gate flagged legitimate code: {line!r}"


def test_reconciliation_may_read_but_never_write_another_contexts_table():
    assert not _fixture_problems(
        "n = db.scalar(select(func.count()).select_from(VehicleParty).where(VehicleParty.vehicle_id.in_(ids)))",
        reconciliation=True,
    )
    assert _fixture_problems("db.execute(delete(VehicleParty))", reconciliation=True)
    assert _fixture_problems("db.add(Dealership(legal_name='x'))", reconciliation=True)
