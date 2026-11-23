"""KAN-783: a test run refuses another checkout's .venv and another checkout's
test database (`tests/checkout_guard.py`, called by conftest's
`pytest_configure`). Unit cases for each rule, then a real pytest run
refused end to end for each."""

import importlib.util
import os
import site
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from tests import checkout_guard

_ROOT = Path(__file__).resolve().parents[1]


def test_this_checkouts_venv_passes():
    assert checkout_guard.foreign_venv(_ROOT, str(_ROOT / ".venv")) is None


def test_an_interpreter_that_is_no_checkouts_venv_is_not_judged():
    # CI runs its system Python with `pip install -e`.
    assert checkout_guard.foreign_venv(_ROOT, "/usr") is None


def test_another_checkouts_venv_is_refused(tmp_path):
    other = tmp_path / "nexotec" / ".venv"
    refusal = checkout_guard.foreign_venv(_ROOT, str(other))
    assert refusal is not None
    assert str(other) in refusal
    assert str(_ROOT / ".venv" / "bin" / "pytest") in refusal


@pytest.mark.parametrize(
    "database",
    [
        "dms_test_agent_a1b2",  # the worktree's own
        "dms_test_agent_a1b2_gw0",  # its xdist worker's
        "dms_test_agent_a1b2_scratch",  # a scratch database derived from it
        "dms_platform_test",  # CI's: outside the per-checkout scheme
        None,
    ],
)
def test_this_checkouts_database_passes(database):
    assert checkout_guard.foreign_database(database, own_slug="agent_a1b2") is None


@pytest.mark.parametrize("database", ["dms_test_root", "dms_test_root_gw1", "dms_test_agent_zz99"])
def test_another_checkouts_database_is_refused(database):
    refusal = checkout_guard.foreign_database(database, own_slug="agent_a1b2")
    assert refusal is not None
    assert database in refusal and "dms_test_agent_a1b2" in refusal


def _hooks():
    spec = importlib.util.spec_from_file_location("_nexotec_hooks_for_kan783", _ROOT / ".claude" / "hooks" / "nexotec_hooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_slug_is_the_hooks():
    hooks = _hooks()
    for name in ("agent-A1b2", "Kan 783 · x", "w" * 60, "---"):
        assert checkout_guard.checkout_slug(Path("/x") / name, is_worktree=True) == hooks.slug(name)[:40]
    assert checkout_guard.checkout_slug(_ROOT, is_worktree=checkout_guard.is_worktree(_ROOT)) == hooks.worktree_slug(
        str(_ROOT)
    )
    assert checkout_guard.checkout_slug(Path("/x/nexotec"), is_worktree=False) == "root"


def _run_one_test(python, env):
    return subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "tests/test_checkout_guard.py::test_another_checkouts_database_is_refused"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def test_a_run_pointed_at_another_checkouts_database_is_refused_before_it_starts(tmp_path):
    own = checkout_guard.checkout_slug(_ROOT, is_worktree=checkout_guard.is_worktree(_ROOT))
    foreign = "dms_test_" + ("other_checkout" if own != "other_checkout" else "root")
    env = dict(os.environ, DMS_TEST_DATABASE_URL=f"postgresql+psycopg://dms:dms@localhost:5432/{foreign}")
    env.pop("PYTEST_XDIST_WORKER", None)
    result = _run_one_test(sys.executable, env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "KAN-783" in result.stdout + result.stderr
    assert foreign in result.stdout + result.stderr


def test_a_run_under_another_checkouts_venv_is_refused_before_it_starts(tmp_path):
    # A bare interpreter in another checkout's `.venv`, given this .venv's
    # packages on PYTHONPATH: what a worktree's PATH pointing at the main
    # checkout's .venv amounts to.
    foreign = tmp_path / "other-checkout" / ".venv"
    venv.create(foreign, with_pip=False, symlinks=True)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(site.getsitepackages()))
    env.pop("PYTEST_XDIST_WORKER", None)
    result = _run_one_test(str(foreign / "bin" / "python"), env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "KAN-783" in result.stdout + result.stderr
    assert str(foreign) in result.stdout + result.stderr
