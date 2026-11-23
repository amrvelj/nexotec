"""KAN-783: a test run refuses another checkout's code and another checkout's
test database (`tests/checkout_guard.py`, called by conftest's
`pytest_configure`). Unit cases for each rule, then one real pytest run
refused end to end."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import checkout_guard

_ROOT = Path(__file__).resolve().parents[1]


def test_this_checkouts_app_passes():
    import app

    assert checkout_guard.foreign_app(_ROOT, app.__file__) is None


def test_another_checkouts_app_is_refused(tmp_path):
    other = tmp_path / "nexotec" / "app" / "__init__.py"
    refusal = checkout_guard.foreign_app(_ROOT, str(other))
    assert refusal is not None
    assert str(other.parent) in refusal
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


def test_the_slug_is_the_hooks_and_bootstraps():
    assert checkout_guard.checkout_slug(Path("/x/nexotec"), is_worktree=False) == "root"
    assert checkout_guard.checkout_slug(Path("/x/.claude/worktrees/agent-A1b2"), is_worktree=True) == "agent_a1b2"
    assert checkout_guard.checkout_slug(Path("/x/" + "w" * 60), is_worktree=True) == "w" * 40


def test_a_run_pointed_at_another_checkouts_database_is_refused_before_it_starts(tmp_path):
    own = checkout_guard.checkout_slug(_ROOT, is_worktree=checkout_guard.is_worktree(_ROOT))
    foreign = "dms_test_" + ("other_checkout" if own != "other_checkout" else "root")
    env = dict(os.environ, DMS_TEST_DATABASE_URL=f"postgresql+psycopg://dms:dms@localhost:5432/{foreign}")
    env.pop("PYTEST_XDIST_WORKER", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "tests/test_checkout_guard.py::test_the_slug_is_the_hooks_and_bootstraps"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "KAN-783" in result.stdout + result.stderr
    assert foreign in result.stdout + result.stderr
