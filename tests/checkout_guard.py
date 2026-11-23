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
  worktree) but not this checkout's own. A database derived from this one
  (`<own>_gw0`, a scratch `<own>_…`) is this checkout's. A name outside the
  `dms_test_` scheme (CI's `dms_platform_test`) is not judged: it belongs to
  no checkout.

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


def _slug(text: str) -> str:
    # The hooks' `slug` (`.claude/hooks/nexotec_hooks.py`) — bootstrap and the
    # check name a worktree's database with it.
    value = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
    return value or "x"


def checkout_slug(root: Path, *, is_worktree: bool) -> str:
    return _slug(root.name)[:40] if is_worktree else "root"


def is_worktree(root: Path) -> bool:
    """A linked worktree's git dir differs from the common dir (the hooks' rule)."""

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    return len(out) == 2 and os.path.normpath(out[0]) != os.path.normpath(out[1])


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


def foreign_database(database: str | None, *, own_slug: str) -> str | None:
    """The refusal when `database` is another checkout's `dms_test_<slug>`."""

    if not database or not database.startswith(_PREFIX):
        return None
    own = _PREFIX + own_slug
    if database == own or database.startswith(own + "_"):
        return None
    return (
        f"this run would use another checkout's test database `{database}`; this checkout's is `{own}`. "
        f"export DMS_TEST_DATABASE_URL with `{own}` (scripts/dev/bootstrap here creates it), "
        f"or run scripts/dev/check, which sets it."
    )
