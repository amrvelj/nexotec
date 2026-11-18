"""KAN-300: scripts/dev/check runs the migration lanes for every app/ file a
migration run loads — the models and everything they import, the app modules
the migrations import themselves, and the data files in those packages —
derived from alembic/env.py's and the migrations' imports rather than kept as
a list. The list missed app/core/enum_type.py, which env.py and every enum
column import.

KAN-732: an app/ change runs a lane whose pytest collects the SPA's
backend-reading twin. The config lane runs this file too.
"""

import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys

import pytest

_CHECK = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dev" / "check"


@pytest.fixture(scope="module")
def check():
    loader = importlib.machinery.SourceFileLoader("dev_check", str(_CHECK))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("dev_check", loader))
    loader.exec_module(module)
    module.PY = sys.executable  # this interpreter has the app installed (CI has no .venv)
    return module


@pytest.mark.parametrize(
    "path",
    [
        "app/core/enum_type.py",  # env.py and every enum column
        "app/core/base.py",  # every model's base class
        "app/core/types.py",  # EncryptedString on customer.tax_id
        "app/customer/models/customer.py",
        "app/platform/reference_data_seed/__init__.py",  # imported by a migration only
        "app/platform/reference_data_seed/countries.csv",  # read by that migration
        "app/core/postal_codes.py",  # imported by a migration only
    ],
)
def test_a_file_the_migrations_load_runs_the_migration_lanes(check, path):
    assert "migrations" in check.lanes_for([path])


def test_an_app_file_the_migrations_do_not_load_does_not(check):
    assert check.lanes_for(["app/sales/api/contracts.py"]) == ["backend", "api-types"]


def test_the_derived_inputs_are_the_models_and_their_imports(check):
    inputs = check.migration_inputs()
    assert inputs is not None
    assert {"app/core/enum_type.py", "app/model_registry.py", "app/db.py", "app/core/config.py"} <= inputs
    assert all(path.startswith("app/") for path in inputs)
    assert "app/platform/reference_data_seed/countries.csv" in inputs  # a migration reads it
    assert not any("/api/" in path for path in inputs)


def test_when_the_inputs_cannot_be_derived_any_app_change_runs_the_lanes(check, monkeypatch):
    monkeypatch.setattr(check, "migration_inputs", lambda: None)
    assert "migrations" in check.lanes_for(["app/sales/api/contracts.py"])
    assert "migrations" not in check.lanes_for(["frontend/apps/dms/src/main.tsx"])


# KAN-732: the SPA's backend-reading twin (KAN-679). A backend-only change must run it, or a new
# refusal reason without its four sentences merges green and leaves main red. Narrowing the
# backend lane's patterns or its pytest selection would drop it silently; this fails instead.
# scripts/dev/check's config lane runs this file, so a diff touching only the check meets it.
_TWIN = "tests/test_frontend_knows_backend_refusals.py"


def test_an_app_change_runs_the_lane_that_collects_the_spa_refusal_twin(check):
    assert "backend" in check.lanes_for(["app/x.py"])
    collected = subprocess.run(
        [sys.executable, *check.backend_pytest(parallel=False), "--collect-only", "-p", "no:cacheprovider"],
        cwd=_CHECK.parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout[-2000:]
    assert any(line.startswith(_TWIN + "::") for line in collected.stdout.splitlines()), (
        f"the backend lane's pytest no longer collects {_TWIN}"
    )
