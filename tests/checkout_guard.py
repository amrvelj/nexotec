"""KAN-783 (CLAUDE.md «Never call another checkout's .venv»): a test run
refuses to start under another checkout's `.venv`, or against another
checkout's test database.

A Claude worktree (`.claude/worktrees/agent-*`) inherits the session's `PATH`
and `DMS_TEST_DATABASE_URL`. A bare `pytest` there runs the main checkout's
`.venv` — its interpreter, its installed dependencies — and truncates the main
checkout's `dms_test_root` under its running check. (`app` itself still comes
from the worktree: pytest puts the rootdir first on `sys.path`, ahead of the
editable install's finder, `pyproject.toml`'s `pythonpath = ["."]`.) Both are
refused here, at `pytest_configure`, before a table is touched:

- **The interpreter is another checkout's `.venv`** — `sys.prefix` is a
  directory named `.venv` whose parent is not this checkout. An interpreter
  that is no checkout's `.venv` (CI's system Python with `pip install -e`) is
  not judged.
- **The test database is another checkout's** — the URL names
  `dms_test_<slug>` (the per-checkout name bootstrap and `scripts/dev/check`
  give it: `root` for the main checkout, the worktree folder's slug for a
  worktree) but not this checkout's own. Only the names derived from this
  one that the suite itself makes are this checkout's (KAN-802,
  `_DERIVED`): `<own>_gw<n>` (a pytest-xdist worker's, `conftest.py`) and,
  optionally after it, a scratch `<own>_isolation_<pid>` or
  `<own>_leftover_<pid>` (`test_postgres_test_isolation.py`) or
  `<own>_kan251_<12 hex>` (`test_migrated_schema_matches_models.py`).
  Anything else under `dms_test_` is refused — a sibling worktree's
  `dms_test_<own>_x` included. A name outside the `dms_test_` scheme (CI's
  `dms_platform_test`, the suite's `dms_kan<n>_<hex>` scratch databases) is
  not judged: it belongs to no checkout.
- **Git cannot say whether this is a worktree** — the database is in the
  `dms_test_` scheme but `git rev-parse` failed, so the own name is unknown.
  Refused rather than assumed `root`: a worktree taking itself for the main
  checkout would accept `dms_test_root`, the database this guard protects
  (KAN-802).

Pure functions, so each refusal is tested (`tests/test_checkout_guard.py`,
which also runs pytest once under a foreign `.venv` and once against a
foreign database). The slug rule is the hooks' (`.claude/hooks/nexotec_hooks.py`
`slug` / `worktree_slug`); the test compares the two.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_PREFIX = "dms_test_"

# What may follow this checkout's own name (KAN-802): a pytest-xdist worker's
# `_gw<n>` (`conftest.py`), then optionally one scratch database the suite
# derives from it — `_isolation_<pid>` / `_leftover_<pid>`
# (`test_postgres_test_isolation.py`), `_kan251_<12 hex>`
# (`test_migrated_schema_matches_models.py`). A new derived name is added
# here, with its unit case.
_DERIVED = re.compile(r"(?:_gw\d+)?(?:_isolation_\d+|_leftover_\d+|_kan251_[0-9a-f]{12})?")


def _slug(text: str) -> str:
    # The hooks' `slug` (`.claude/hooks/nexotec_hooks.py`) — bootstrap and the
    # check name a worktree's database with it.
    value = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
    return value or "x"


def checkout_slug(root: Path, *, is_worktree: bool) -> str:
    return _slug(root.name)[:40] if is_worktree else "root"


def is_worktree(root: Path) -> bool | None:
    """A linked worktree's git dir differs from the common dir (the hooks' rule).

    None when git cannot say (not installed, not a repository, an answer
    that is not two paths): the caller refuses instead of guessing (KAN-802).
    """

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(out) != 2:
        return None
    return os.path.normpath(out[0]) != os.path.normpath(out[1])


def foreign_venv(root: Path, prefix: str) -> str | None:
    """The refusal when `prefix` (`sys.prefix`) is another checkout's `.venv`."""

    venv = Path(prefix).resolve()
    if venv.name != ".venv" or venv.parent == root.resolve():
        return None
    return (
        f"this run uses another checkout's .venv ({venv}); the tests are {root / 'tests'}. "
        f"The `pytest` on PATH belongs to that checkout. Run this checkout's own: "
        f"{root / '.venv' / 'bin' / 'pytest'} (after scripts/dev/bootstrap here)."
    )


def judged(database: str | None) -> bool:
    """Whether `database` is in the per-checkout `dms_test_` scheme at all."""

    return bool(database) and database.startswith(_PREFIX)


def foreign_database(database: str | None, *, own_slug: str) -> str | None:
    """The refusal when `database` is another checkout's `dms_test_<slug>`."""

    if not judged(database):
        return None
    own = _PREFIX + own_slug
    if database.startswith(own) and _DERIVED.fullmatch(database[len(own) :]):
        return None  # the own name itself, or one the suite derives from it
    return (
        f"this run would use another checkout's test database `{database}`; this checkout's is `{own}`. "
        f"export DMS_TEST_DATABASE_URL with `{own}` (scripts/dev/bootstrap here creates it), "
        f"or run scripts/dev/check, which sets it."
    )


def unknown_checkout(root: Path) -> str:
    """The refusal when git cannot say whether `root` is a worktree (KAN-802)."""

    return (
        f"git could not say whether {root} is a worktree or the main checkout, so this checkout's "
        f"test database name is unknown; refusing rather than assuming the main checkout's `{_PREFIX}root`. "
        f"Make `git -C {root} rev-parse --git-dir --git-common-dir` work here, "
        f"or point DMS_TEST_DATABASE_URL at a database outside the `{_PREFIX}` scheme."
    )


def database_refusal(root: Path, database: str | None) -> str | None:
    """The refusal for running against `database` from checkout `root`, or None.

    Git is asked only for a database in the `dms_test_` scheme, so CI's
    `dms_platform_test` never depends on it.
    """

    if not judged(database):
        return None
    worktree = is_worktree(root)
    if worktree is None:
        return unknown_checkout(root)
    return foreign_database(database, own_slug=checkout_slug(root, is_worktree=worktree))
