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
        "dms_test_agent_a1b2_gw12",
        # KAN-802: the scratch databases the suite derives from it, alone or after a worker's suffix
        "dms_test_agent_a1b2_isolation_4711",  # test_postgres_test_isolation.py
        "dms_test_agent_a1b2_gw3_isolation_4711",
        "dms_test_agent_a1b2_leftover_4711",
        "dms_test_agent_a1b2_kan251_0123456789ab",  # test_migrated_schema_matches_models.py
        "dms_test_agent_a1b2_gw0_kan251_0123456789ab",
        "dms_platform_test",  # CI's: outside the per-checkout scheme
        "dms_kan627_0123456789ab",  # the suite's own scratch scheme
        None,
    ],
)
def test_this_checkouts_database_passes(database):
    assert checkout_guard.foreign_database(database, own_slug="agent_a1b2") is None


@pytest.mark.parametrize(
    "database",
    [
        "dms_test_root",
        "dms_test_root_gw1",
        "dms_test_agent_zz99",
        # KAN-802: a sibling worktree whose slug starts with this one's
        "dms_test_agent_a1b2_x",
        "dms_test_agent_a1b2_x_gw0",
        # a suffix the suite never makes
        "dms_test_agent_a1b2_scratch",
        "dms_test_agent_a1b2_gw",
        "dms_test_agent_a1b2_kan251_xyz",
    ],
)
def test_another_checkouts_database_is_refused(database):
    refusal = checkout_guard.foreign_database(database, own_slug="agent_a1b2")
    assert refusal is not None
    assert database in refusal and "dms_test_agent_a1b2" in refusal


@pytest.mark.parametrize("database", ["dms_test_root_foo", "dms_test_root_foo_gw0", "dms_test_rootx"])
def test_the_main_checkout_refuses_a_worktree_whose_slug_starts_with_root(database):
    # KAN-802: `root` is the main checkout's slug; a worktree folder named `root-foo` is `root_foo`.
    assert checkout_guard.foreign_database(database, own_slug="root") is not None


def test_every_derived_name_the_suite_makes_is_listed():
    # The two suffixes built from DMS_TEST_DATABASE_URL by the suite itself: if one of these modules
    # changes its scheme, `_DERIVED` must follow (or a child pytest run is refused with code 4).
    isolation = (_ROOT / "tests" / "test_postgres_test_isolation.py").read_text()
    assert '{url.database}_isolation_{os.getpid()}' in isolation
    assert '{url.database}_leftover_{os.getpid()}' in isolation
    schema = (_ROOT / "tests" / "test_migrated_schema_matches_models.py").read_text()
    assert "{make_url(base).database}_kan251_{uuid.uuid4().hex[:12]}" in schema


def _git_fails(*args, **kwargs):
    raise FileNotFoundError("git")


def test_when_git_is_unavailable_is_worktree_says_it_does_not_know(monkeypatch):
    monkeypatch.setattr(checkout_guard.subprocess, "run", _git_fails)
    assert checkout_guard.is_worktree(_ROOT) is None


def test_outside_a_repository_is_worktree_says_it_does_not_know(tmp_path, monkeypatch):
    folder = tmp_path / "not-a-repo"
    folder.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # git must not find a repository above it
    assert checkout_guard.is_worktree(folder) is None


def test_when_git_is_unavailable_a_per_checkout_database_is_refused(monkeypatch):
    # KAN-802: never assume `root` — a worktree would then accept dms_test_root.
    monkeypatch.setattr(checkout_guard.subprocess, "run", _git_fails)
    refusal = checkout_guard.database_refusal(_ROOT, "dms_test_root")
    assert refusal is not None
    assert "git could not say" in refusal and str(_ROOT) in refusal


@pytest.mark.parametrize("database", ["dms_platform_test", None])
def test_a_database_outside_the_scheme_never_asks_git(monkeypatch, database):
    # CI runs `dms_platform_test`; it must not depend on git answering.
    def _must_not_run(*args, **kwargs):
        raise AssertionError("git was asked")

    monkeypatch.setattr(checkout_guard.subprocess, "run", _must_not_run)
    assert checkout_guard.database_refusal(_ROOT, database) is None


def test_this_checkouts_own_database_passes_the_whole_rule():
    own = checkout_guard.checkout_slug(_ROOT, is_worktree=checkout_guard.is_worktree(_ROOT))
    assert checkout_guard.database_refusal(_ROOT, f"dms_test_{own}") is None
    assert checkout_guard.database_refusal(_ROOT, f"dms_test_{own}_gw0") is None


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
