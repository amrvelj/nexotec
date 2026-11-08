"""KAN-485 — no provider is ever asked for its whole catalogue.

Anto's ruling, 2026-10-29: the auto-i-dat agreement permits single lookups
only (identAPI test agreement §2, §9: no retrieval of all vehicles, no storing
or passing them on), and the demo provider follows the same rule. ADR-023 is
amended accordingly. The full seed and the daily `FzKeyChanged` delta were
removed; this guard keeps them out:

  1. No adapter offers the changed-keys list a mirror is built on
     (`list_changed_keys`, the `FzKeyChanged` Datenname).
  2. Nothing under `app/` or `scripts/` calls or names it, and the old bulk
     entry points (`seed_tenant_catalogue`, `run_daily_delta_for_tenant`) do
     not come back under those names.
  3. No daily job reaches a provider at all.
  4. **The identAPI's pages are never walked** (ADR-112 (4), KAN-548): the
     provider page parameter (`page[number]`) is written in the identAPI
     adapter alone, and no catalogue query (`search_catalogue_variants`,
     `facet_values`, `list_catalogue_brands`, `list_catalogue_model_groups`,
     and since KAN-211 the detail loaders and `load_on_open`) is called
     inside a `for`/`while` body anywhere under `app/` or `scripts/` — one
     user request is one provider page, and a loop over pages is how a
     mirror would be built. **One ruled exception** (ADR-123, KAN-211): the
     option list of the ONE car the user opened is followed page by page in
     `fetch_options`, bounded by `OPTIONS_PAGE_CAP`, pinned by
     `test_the_options_walk_is_bounded_by_the_page_cap`.

Self-tests prove the scanner catches each shape.
"""

import ast
import uuid
from pathlib import Path

import pytest

from app import worker
from app.core import daily_scheduler
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter
from app.integration.adapters.base import ProviderAdapter
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway

_REPO = Path(__file__).resolve().parent.parent.parent
_SCANNED_ROOTS = (_REPO / "app", _REPO / "scripts")

_FORBIDDEN_DATENNAMEN = {"FzKeyChanged"}
_FORBIDDEN_NAMES = {"list_changed_keys", "seed_tenant_catalogue", "run_daily_delta_for_tenant"}


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _FORBIDDEN_DATENNAMEN:
            found.append(f"string {node.value!r} (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            found.append(f"attribute .{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            found.append(f"name {node.id} (line {node.lineno})")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in _FORBIDDEN_NAMES:
            found.append(f"def {node.name} (line {node.lineno})")
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in _FORBIDDEN_NAMES:
            found.append(f"import {node.name} (line {getattr(node, 'lineno', '?')})")
    return found


_PAGE_PARAMETER = "page[number]"
_PAGE_PARAMETER_HOME = "app/integration/adapters/auto_i_dat_ident.py"
# The adapter's catalogue queries and the service functions that wrap them: a
# loop over any of them is how a mirror would be built.
_PAGED_QUERIES = {
    "search_catalogue_variants",
    "facet_values",
    "list_catalogue_brands",
    "list_catalogue_model_groups",
    "browse_variants",
    "compute_facets",
    "list_brands",
    "list_model_groups",
    # the service's own per-field helper: a loop over it is a loop over facet requests
    "_facet",
    # KAN-211 (ADR-123): the details of ONE opened car. A loop over any of them
    # — or over `load_on_open` itself — is a walk over cars, i.e. a mirror.
    "fetch_variant_with_colours",
    "fetch_options",
    "fetch_tyre_specs",
    "fetch_images",
    "fetch_energy_label",
    "load_on_open",
    "reload_details",
}
_LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# The one loop allowed by name (ADR-112 (4), KAN-548): `compute_facets` asks one
# facet field after another over the fixed `FACET_FIELDS` tuple — a bound written
# in code, pinned below (`test_the_facet_loop_is_bounded_by_the_field_tuple`) —
# never over pages. Nothing else is allow-listed.
_BOUNDED_FIELD_LOOPS = {("app/vehicle/services/catalogue_browse.py", "compute_facets")}


def _paged_query_calls_in_loops(source: str, *, path: str = "") -> list[str]:
    """Every call of a catalogue query inside a loop body — `for`, `while`
    and every comprehension (nested functions included: a helper defined in
    the loop is still the loop's) — outside the allow-listed functions."""

    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (path, node.name) in _BOUNDED_FIELD_LOOPS:
            allowed.update(id(inner) for inner in ast.walk(node))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _LOOPS) or id(node) in allowed:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                callee = inner.func
                name = (
                    callee.attr
                    if isinstance(callee, ast.Attribute)
                    else callee.id
                    if isinstance(callee, ast.Name)
                    else None
                )
                if name in _PAGED_QUERIES:
                    found.append(f"{name}() inside a loop (line {inner.lineno})")
    return found


def _page_parameter_mentions(source: str) -> list[str]:
    return [
        f"string {node.value!r} (line {node.lineno})"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _PAGE_PARAMETER in node.value
    ]


def _python_files() -> list[Path]:
    return sorted(path for root in _SCANNED_ROOTS for path in root.rglob("*.py"))


def test_the_scan_covers_the_adapters_and_the_sync():
    files = {path.relative_to(_REPO).as_posix() for path in _python_files()}
    for expected in (
        "app/integration/adapters/auto_i_dat_soap.py",
        "app/integration/adapters/auto_i_dat_mock.py",
        "app/vehicle/services/catalogue_sync.py",
        "app/integration/daily_jobs.py",
        "scripts/verify_auto_i_dat.py",
    ):
        assert expected in files, expected


@pytest.mark.parametrize(
    "adapter", [ProviderAdapter, MockAutoIDatAdapter, AutoIDatSoapAdapter], ids=lambda a: a.__name__
)
def test_no_adapter_offers_the_changed_keys_list(adapter):
    assert not hasattr(adapter, "list_changed_keys"), (
        f"{adapter.__name__} offers list_changed_keys again — FzKeyChanged is what a full catalogue mirror is "
        "built on, which the provider contract forbids (KAN-485, ADR-023 as amended 2026-10-29)."
    )


def test_nothing_in_app_or_scripts_retrieves_the_whole_catalogue():
    offending = {
        path.relative_to(_REPO).as_posix(): hits for path in _python_files() if (hits := _violations(path.read_text()))
    }
    assert not offending, (
        f"Bulk catalogue retrieval is back — single lookups only (KAN-485, ADR-023 as amended 2026-10-29): {offending}"
    )


def test_no_daily_job_reaches_a_provider(db_session, monkeypatch):
    """Every registered daily job, run against a tenant with an enabled demo
    connection, never resolves a provider adapter — the step every provider
    call goes through (`gateway._adapter_factory`). The daily catalogue delta
    used to be the first thing `integration.daily_jobs` did.
    """

    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )

    resolved: list[uuid.UUID] = []

    def _record(db, connection):
        resolved.append(connection.id)
        raise AssertionError("a daily job reached a provider")

    monkeypatch.setattr(gateway, "_adapter_factory", _record)
    monkeypatch.setattr(daily_scheduler, "_REGISTRY", {})
    worker.register_daily_jobs()
    jobs = dict(daily_scheduler._REGISTRY)
    assert "integration.daily_jobs" in jobs, (
        "the integration daily job is not registered — the guard would pass vacuously"
    )
    for name, job in jobs.items():
        try:
            job(db_session)
        except AssertionError:
            raise
        except Exception:  # noqa: BLE001 - a job failing for another reason is not this guard's question
            db_session.rollback()
        assert not resolved, f"daily job {name!r} reached a provider: single lookups only (KAN-485)"


def test_the_identapi_page_parameter_is_written_in_the_adapter_alone():
    """ADR-112 (4): a page number the user did not ask for is a page nobody
    may fetch — the parameter has one home, and the adapter sends exactly
    the page it was given."""

    offending = {
        path.relative_to(_REPO).as_posix(): hits
        for path in _python_files()
        if path.relative_to(_REPO).as_posix() != _PAGE_PARAMETER_HOME
        and (hits := _page_parameter_mentions(path.read_text()))
    }
    assert not offending, f"the identAPI page parameter outside the adapter: {offending}"
    assert _page_parameter_mentions((_REPO / _PAGE_PARAMETER_HOME).read_text()), (
        "the adapter no longer pages — the guard would pass vacuously"
    )


def test_no_catalogue_query_runs_inside_a_loop():
    """A loop over a catalogue query — the adapter's or the service wrapper's,
    a `for`, a `while` or a comprehension — is how a mirror would be built; one
    user request is one provider page (KAN-548). The one loop allowed is
    `compute_facets`' walk over the fixed `FACET_FIELDS` tuple, allow-listed by
    name and bounded by the test below."""

    offending = {
        rel: hits
        for path in _python_files()
        if (hits := _paged_query_calls_in_loops(path.read_text(), path=(rel := path.relative_to(_REPO).as_posix())))
    }
    assert not offending, f"a catalogue query inside a loop (ADR-112 (4), KAN-548): {offending}"


def test_the_facet_loop_is_bounded_by_the_field_tuple():
    """The allow-listed loop asks one request per field of a tuple written in
    code: at most four, never a page, and `compute_facets` never pages."""

    from app.vehicle.services import catalogue_browse

    assert 1 <= len(catalogue_browse.FACET_FIELDS) <= 4
    source = (_REPO / "app/vehicle/services/catalogue_browse.py").read_text()
    tree = ast.parse(source)
    facets = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "compute_facets")
    names = {
        (c.func.attr if isinstance(c.func, ast.Attribute) else c.func.id if isinstance(c.func, ast.Name) else None)
        for c in ast.walk(facets)
        if isinstance(c, ast.Call)
    }
    assert "search_catalogue_variants" not in names and "browse_variants" not in names
    loops = [n for n in ast.walk(facets) if isinstance(n, _LOOPS)]
    assert len(loops) == 1 and isinstance(loops[0], ast.For), "one loop, a plain for — no while, no comprehension"
    assert isinstance(loops[0].iter, ast.Name) and loops[0].iter.id == "FACET_FIELDS"
    # the allow-list is not vacuous: without it the scanner catches this very loop
    assert _paged_query_calls_in_loops(source, path="app/vehicle/services/other.py")
    # the allow-list is this one function and nothing else
    assert _BOUNDED_FIELD_LOOPS == {("app/vehicle/services/catalogue_browse.py", "compute_facets")}


def test_the_options_walk_is_bounded_by_the_page_cap():
    """KAN-211 (ADR-123): `fetch_options` is the one place a paged identAPI
    endpoint is followed past its first page — the option list of the ONE car
    the user opened (Anto, 2026-11-08: every option, searchable). The walk is
    a plain `for` over `range(1, OPTIONS_PAGE_CAP + 1)`, the cap a constant
    of at most 30, and the loop body sends requests (`_request`) and calls no
    catalogue query; nothing else in the adapter loops over a page."""

    from app.integration.adapters import auto_i_dat_ident

    assert 1 <= auto_i_dat_ident.OPTIONS_PAGE_CAP <= 30
    source = (_REPO / _PAGE_PARAMETER_HOME).read_text()
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    fetch_options = functions["fetch_options"]
    loops = [n for n in ast.walk(fetch_options) if isinstance(n, _LOOPS)]
    assert len(loops) == 1 and isinstance(loops[0], ast.For), "one loop, a plain for"
    bound = loops[0].iter
    assert isinstance(bound, ast.Call) and isinstance(bound.func, ast.Name) and bound.func.id == "range"
    assert any(isinstance(n, ast.Name) and n.id == "OPTIONS_PAGE_CAP" for n in ast.walk(bound))
    assert not _paged_query_calls_in_loops(source, path=_PAGE_PARAMETER_HOME), "the walk calls no catalogue query"
    # every other method of the adapter that sends a page parameter does so once, outside a loop
    for name, function in functions.items():
        if name == "fetch_options":
            continue
        for loop in (n for n in ast.walk(function) if isinstance(n, _LOOPS)):
            assert not _page_parameter_mentions(ast.unparse(loop)), f"{name} pages inside a loop"


# -- self-tests: the scanner catches each shape ------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "for n in range(1, 50):\n    page = adapter.search_catalogue_variants(vehicle_kind_code='01', brand=b, page_number=n)",
        "while more:\n    more = fetch(adapter.facet_values(field='fuel', vehicle_kind_code='01', brand=b))",
        "for kind in kinds:\n    def go():\n        return adapter.list_catalogue_brands(vehicle_kind_code=kind)",
        "for brand in brands:\n    adapter.list_catalogue_model_groups(vehicle_kind_code='01', brand=brand)",
        "pages = [adapter.search_catalogue_variants(vehicle_kind_code='01', brand=b, page_number=n) for n in range(9)]",
        "pages = (adapter.search_catalogue_variants(vehicle_kind_code='01', brand=b, page_number=n) for n in range(9))",
        "facets = {f: _facet(adapter.facet_values(field=f, vehicle_kind_code='01', brand=b)) for f in FIELDS}",
        "for n in range(1, 99):\n    catalogue_browse.browse_variants(db, tenant_id=t, actor_id=a, vehicle_kind=k, brand=b, model_group_key=None, filters=f, sort=[], page=n)",
        "rows = {b: list_brands(db, tenant_id=t, actor_id=a, vehicle_kind=b) for b in kinds}",
        "for f in fields:\n    coded[f] = _facet(db, provider, actor_id=a, kind_code=k, brand=b, model_group_key=None, api_field=f)",
    ],
)
def test_the_loop_scanner_catches(source):
    assert _paged_query_calls_in_loops(source)


def test_the_loop_scanner_allow_lists_only_the_named_function():
    source = "def compute_facets():\n    for f in FACET_FIELDS:\n        adapter.facet_values(field=f, vehicle_kind_code='01', brand=b)\n"
    assert not _paged_query_calls_in_loops(source, path="app/vehicle/services/catalogue_browse.py")
    assert _paged_query_calls_in_loops(source, path="app/vehicle/services/other.py"), "the allow-list is per file"
    assert _paged_query_calls_in_loops(
        source.replace("compute_facets", "other_function"), path="app/vehicle/services/catalogue_browse.py"
    )


def test_the_loop_scanner_leaves_one_page_alone():
    assert not _paged_query_calls_in_loops(
        "page = adapter.search_catalogue_variants(vehicle_kind_code='01', brand=b, page_number=page_number)\n"
        "rows = [store(m) for m in page.items]\n"
        "for api_field in FACET_FIELDS:\n    coded[api_field] = resolve(values[api_field])\n"
    )
    assert not _paged_query_calls_in_loops(
        "def list_brands(db, *, tenant_id, actor_id, vehicle_kind):\n"
        "    return _call(db, provider, call=lambda adapter: adapter.list_catalogue_brands(vehicle_kind_code=kind_code))\n"
    ), "a lambda is not a loop"
    assert _page_parameter_mentions("params.append(('page[number]', str(n)))")
    assert not _page_parameter_mentions("params.append(('page[size]', '10'))")


@pytest.mark.parametrize(
    "source",
    [
        'adapter._suchen("FzKeyChanged", {})',
        "keys = adapter.list_changed_keys(since=today)",
        "def list_changed_keys(self, *, since): ...",
        "def seed_tenant_catalogue(db, *, tenant_id): ...",
        "from app.vehicle.public import run_daily_delta_for_tenant",
        "run_daily_delta_for_tenant(db, tenant_id=tenant_id)",
    ],
)
def test_the_scanner_catches(source):
    assert _violations(source)


def test_the_scanner_leaves_single_lookups_alone():
    assert not _violations(
        'adapter._suchen("Fahrzeuge", {"FzKey": fz_key})\n'
        "sync_catalogue_variant(db, tenant_id=tenant_id, fz_key=fz_key)\n"
        '"""A docstring may mention FzKeyChanged in prose."""\n'
    )
