"""KAN-300: scripts/dev/check runs the migration lanes for every app/ file that
alembic/env.py loads — the models and everything they import from app/core —
derived from env.py's imports rather than kept as a list. The list missed
app/core/enum_type.py, which env.py and every enum column import.
"""

import importlib.machinery
import importlib.util
import pathlib
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
    assert all(path.startswith("app/") and path.endswith(".py") for path in inputs)
    assert not any("/api/" in path for path in inputs)


def test_when_the_inputs_cannot_be_derived_any_app_change_runs_the_lanes(check, monkeypatch):
    monkeypatch.setattr(check, "migration_inputs", lambda: None)
    assert "migrations" in check.lanes_for(["app/sales/api/contracts.py"])
    assert "migrations" not in check.lanes_for(["frontend/apps/dms/src/main.tsx"])
