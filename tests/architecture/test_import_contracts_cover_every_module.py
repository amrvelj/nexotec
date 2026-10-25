"""Architecture test (KAN-120): CLAUDE.md rule 3 — `<context>.public` is the
only door into a context, and import-linter is what enforces it.

import-linter's `forbidden` contract has no "every module except public"
form, so each context's contract in `pyproject.toml` names its modules one by
one. Before KAN-120 the lists named only `models`, `services` and `api` (plus
integration's `adapters`): `schemas`, `consumers`, `reconciliation`,
`daily_jobs` and the rest were reachable from every other context, and
nothing failed. A list is only as good as its upkeep, so this test fails when
a context gains a top-level module or package that is neither `public` nor
forbidden in its contract, when a context with modules has no contract at
all, and when a contract stops naming every other context as a source.

What it does not prove: that the contracts pass — `lint-imports` (CI's
import-linter job, `scripts/dev/check`) does that.
"""

import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

# The twelve bounded contexts (CLAUDE.md). test_adr_047_no_shared_transaction.py
# fails when app/ gains a package that is neither one of these nor declared
# wiring; this test relies on that and repeats only the names.
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


def _contract_name(context: str) -> str:
    return f"Only app.{context}.public is reachable from outside {context}"


def _contracts() -> dict[str, dict]:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        contracts = tomllib.load(f)["tool"]["importlinter"]["contracts"]
    return {contract["name"]: contract for contract in contracts}


def _top_level_modules(context: str) -> set[str]:
    """Every importable name directly under app/<context>/ but `public`: a
    `.py` file, or a directory (a package, or a namespace package without
    `__init__.py` — importable all the same)."""

    names: set[str] = set()
    for entry in (_APP_ROOT / context).iterdir():
        if entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
            names.add(entry.stem)
        elif entry.is_dir() and entry.name != "__pycache__":
            names.add(entry.name)
    names.discard("public")
    return names


def _contexts_with_modules() -> list[str]:
    return [context for context in _CONTEXTS if _top_level_modules(context)]


def test_every_context_directory_exists():
    missing = [context for context in _CONTEXTS if not (_APP_ROOT / context).is_dir()]
    assert not missing, f"CLAUDE.md's contexts with no app/<context>/ directory: {missing}"


@pytest.mark.parametrize("context", _contexts_with_modules())
def test_every_module_but_public_is_forbidden_to_the_other_contexts(context):
    contract = _contracts().get(_contract_name(context))
    assert contract is not None, (
        f"app/{context}/ has modules ({sorted(_top_level_modules(context))}) but pyproject.toml has no "
        f"import-linter contract named {_contract_name(context)!r}: add one, same shape as the others."
    )
    listed = set(contract["forbidden_modules"])
    expected = {f"app.{context}.{name}" for name in _top_level_modules(context)}
    assert listed == expected, (
        f"{_contract_name(context)!r} must forbid every top-level module of app/{context}/ but public. "
        f"Not listed: {sorted(expected - listed)}. Listed but not in app/{context}/: {sorted(listed - expected)}."
    )


@pytest.mark.parametrize("context", _contexts_with_modules())
def test_every_other_context_is_a_source_and_nothing_is_ignored(context):
    contract = _contracts()[_contract_name(context)]
    assert contract["type"] == "forbidden"
    assert set(contract["source_modules"]) == {f"app.{other}" for other in _CONTEXTS if other != context}
    # Rule 3: no suppression mechanism, of any kind.
    assert "ignore_imports" not in contract
    assert "unmatched_ignore_imports_alerting" not in contract
