"""Architecture test (KAN-243; Anto, 2026-10-12: created_by / updated_by are
checked everywhere; CLAUDE.md rule 10): every mapped table with a
`created_by` or `updated_by` column is covered by its context's nightly
reconciliation — a nullable `<table>.<column> -> user.id` ReferenceCheck,
as `app.platform.public.actor_reference_checks` builds it.

A new table picks the check up by being added to its context's
`ACTOR_TABLES` in `app/<context>/reconciliation.py`; this test fails until it
is. A context with such columns but no reconciliation module registered in
`app.reconciliation_runner` fails too — except the one named below, whose
table is not reconciled yet, for a stated reason. A new entry there is a
decision for Anto, not a convenience. (platform was the other one until
KAN-318 gave it a job and stopped scripts/seed_staging_demo.py stamping a
random actor on the rows it creates.)

Every model module is imported first (as in test_no_cross_context_mapping),
so the gate never depends on `app.model_registry` listing it.
"""

import importlib
from pathlib import Path
from types import ModuleType

from sqlalchemy import inspect
from sqlalchemy.orm import Mapper

import app.model_registry  # noqa: F401  (registers every mapped class on Base)
from app import reconciliation_runner
from app.core.reconciliation import ReferenceCheck, StateCheck
from app.db import Base
from app.platform.public import User

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

for _file in sorted([*_APP_ROOT.glob("*/models/*.py"), *(_APP_ROOT / "core").glob("*_model.py")]):
    if _file.name != "__init__.py":
        importlib.import_module(".".join(_file.relative_to(_REPO_ROOT).with_suffix("").parts))

_ACTOR_COLUMNS = ("created_by", "updated_by")

# context -> why its actor columns are not reconciled.
_NOT_RECONCILED: dict[str, str] = {
    "core": "daily_job_run: app.core has no reconciliation module, and nothing writes its actor columns.",
}


def _context_of(cls: type) -> str:
    parts = cls.__module__.split(".")
    assert parts[0] == "app" and len(parts) >= 3, f"{cls.__module__}.{cls.__qualname__} is outside a context"
    return parts[1]


def _actor_columns(mapper: Mapper) -> list[str]:
    return [name for name in _ACTOR_COLUMNS if name in mapper.columns]


def _targets_user(check: ReferenceCheck) -> bool:
    """User itself, or an alias of it: actor_reference_checks targets an
    alias so that platform's user.created_by -> user.id self-join compiles
    (KAN-318). Anything else — another table, a subquery — is not User."""

    return inspect(check.target_model).class_ is User


def _covers(check: ReferenceCheck | StateCheck, cls: type, column: str) -> bool:
    return (
        isinstance(check, ReferenceCheck)
        and check.source_model is cls
        and check.source_fk_column.key == column
        and _targets_user(check)
        and check.target_id_column.key == "id"
        and check.nullable
    )


def uncovered_actor_columns(
    mappers: list[Mapper], checks_of: dict[str, list[ReferenceCheck | StateCheck]]
) -> list[str]:
    """Every `<table>.<actor column>` among `mappers` that no check of its
    context covers. A function of its inputs, so the negative case below
    can run it with a check removed."""

    missing: list[str] = []
    for mapper in mappers:
        cls = mapper.class_
        context = _context_of(cls)
        if context in _NOT_RECONCILED:
            continue
        for column in _actor_columns(mapper):
            if not any(_covers(check, cls, column) for check in checks_of.get(context, [])):
                missing.append(f"{context}: {cls.__tablename__}.{column} ({cls.__module__}.{cls.__qualname__})")
    return sorted(missing)


def _registered_jobs() -> dict[str, ModuleType]:
    return {job.CONTEXT: job for job in reconciliation_runner._JOBS}


def _mappers() -> list[Mapper]:
    # Test-only models (tests/demo_models.py) share Base; they are not app tables.
    return [m for m in Base.registry.mappers if m.class_.__module__.startswith("app.")]


def test_every_context_with_actor_columns_has_a_registered_reconciliation_job():
    contexts = {_context_of(m.class_) for m in _mappers() if _actor_columns(m)} - _NOT_RECONCILED.keys()

    assert contexts, "found no actor columns at all — the gate is looking at nothing"
    assert sorted(contexts - _registered_jobs().keys()) == []


def test_every_created_by_and_updated_by_column_is_reconciled_against_user():
    checks_of = {context: list(job.CHECKS) for context, job in _registered_jobs().items()}

    assert uncovered_actor_columns(_mappers(), checks_of) == []


def test_the_gate_reports_a_table_whose_actor_check_is_missing():
    from app.sales.models.deal import SalesDeal

    checks_of = {context: list(job.CHECKS) for context, job in _registered_jobs().items()}
    checks_of["sales"] = [c for c in checks_of["sales"] if getattr(c, "source_model", None) is not SalesDeal]

    assert uncovered_actor_columns(_mappers(), checks_of) == [
        "sales: sales_deal.created_by (app.sales.models.deal.SalesDeal)",
        "sales: sales_deal.updated_by (app.sales.models.deal.SalesDeal)",
    ]


def test_the_not_reconciled_contexts_still_have_actor_columns():
    """An entry above that no longer applies is removed, not kept."""

    contexts = {_context_of(m.class_) for m in _mappers() if _actor_columns(m)}

    assert set(_NOT_RECONCILED) <= contexts
    assert not set(_NOT_RECONCILED) & _registered_jobs().keys()
