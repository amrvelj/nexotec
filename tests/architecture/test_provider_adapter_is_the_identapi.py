"""KAN-550 (ADR-111): the vehicle-data adapter contract is the identAPI's,
and nothing of the SOAP «Webservice Fahrzeuge» protocol is left to read as
the integration.

`ProviderAdapter` lists exactly the identAPI operations the identification
(KAN-547), browse (KAN-548), details (KAN-211), VIN (KAN-549) and valuation
(KAN-552) tickets define; the demo provider implements exactly that set, and
the identAPI adapter that set plus its access check and licence-option probes
(KAN-488), which `entitlement_probes` calls by name. No SOAP transport, parser
or AES module remains, and no SOAP-stack dependency is declared.
"""

import ast
import inspect
import tomllib
from pathlib import Path

from app.integration.adapters.auto_i_dat_ident import AutoIDatIdentAdapter
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.base import ProviderAdapter

ROOT = Path(__file__).resolve().parents[2]

IDENTAPI_OPERATIONS = frozenset(
    {
        # identification (KAN-547)
        "identify_by_vrn",
        "identify_by_plate",
        "find_variants_by_type_approval",
        "find_variants_by_werkscode",
        "fetch_variant",
        "find_best_match",
        "fetch_captions",
        # browse (KAN-548)
        "list_catalogue_brands",
        "list_catalogue_model_groups",
        "search_catalogue_variants",
        "facet_values",
        # details on open (KAN-211)
        "fetch_variant_with_colours",
        "fetch_options",
        "fetch_tyre_specs",
        "fetch_images",
        "fetch_energy_label",
        # VIN (KAN-549)
        "identify_by_vin",
        # valuation (KAN-552)
        "fetch_vehicle_valuation",
        "fetch_residual_table",
    }
)

# KAN-488: «Test connection» — the access check and one probe per licence option.
IDENTAPI_CONNECTION_TEST = frozenset(
    {
        "fetch_code_captions",
        "probe_ident_basis",
        "probe_ausstattung",
        "probe_bewertung",
        "probe_vin",
        "probe_pneu",
        "probe_energielabel",
    }
)

SOAP_MODULES = (
    "app/integration/adapters/auto_i_dat_soap.py",
    "app/integration/adapters/auto_i_dat_parse.py",
    "app/integration/adapters/aes_decrypt.py",
    "scripts/verify_auto_i_dat.py",
)
SOAP_STACK = ("zeep", "requests", "urllib3", "lxml")


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, member in vars(cls).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }


def test_the_protocol_is_exactly_the_identapi_operations():
    declared = _public_methods(ProviderAdapter)
    assert declared - IDENTAPI_OPERATIONS == set(), "Protocol methods no identAPI ticket defines"
    assert IDENTAPI_OPERATIONS - declared == set(), "identAPI operations missing from the Protocol"


def test_the_demo_provider_implements_exactly_the_protocol():
    assert _public_methods(MockAutoIDatAdapter) == set(IDENTAPI_OPERATIONS)


def test_the_identapi_adapter_implements_the_protocol_and_its_connection_test_only():
    assert _public_methods(AutoIDatIdentAdapter) == set(IDENTAPI_OPERATIONS | IDENTAPI_CONNECTION_TEST)


def test_the_demo_provider_logs_its_calls_under_identapi_operation_names():
    """A dev or staging call log reads like a real one (KAN-486): every name
    the mock meters a request under is one of the identAPI adapter's `OP_*`."""

    from app.integration.adapters import auto_i_dat_ident

    identapi_names = {value for name, value in vars(auto_i_dat_ident).items() if name.startswith("OP_")}
    tree = ast.parse((ROOT / "app/integration/adapters/auto_i_dat_mock.py").read_text(encoding="utf-8"))
    metered = {
        decorator.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "_request"
        and isinstance(decorator.args[0], ast.Constant)
    }
    assert len(metered) > 10
    assert metered - identapi_names == set()


def test_no_soap_module_is_left():
    assert [path for path in SOAP_MODULES if (ROOT / path).exists()] == []


def test_no_soap_stack_dependency_is_declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = [*project["dependencies"], *(d for group in project.get("optional-dependencies", {}).values() for d in group)]
    names = {dep.split(";")[0].split("[")[0].split("<")[0].split(">")[0].split("=")[0].split("~")[0].strip().lower() for dep in declared}
    assert names & set(SOAP_STACK) == set()


def test_no_application_module_imports_the_soap_stack():
    hits = []
    for path in sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            else:
                continue
            hits += [f"{path.relative_to(ROOT)}:{node.lineno} {root}" for root in roots if root in SOAP_STACK]
    assert hits == []
