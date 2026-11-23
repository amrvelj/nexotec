"""KAN-783 (CLAUDE.md «Never call another checkout's .venv»): a test run
refuses to start when it would test another checkout's code, or write into
another checkout's test database.

A Claude worktree (`.claude/worktrees/agent-*`) inherits the session's `PATH`
and `DMS_TEST_DATABASE_URL`. A bare `pytest` there runs the main checkout's
`.venv` — whose editable install imports the **main** checkout's `app` — and
truncates the main checkout's `dms_test_root` under its running check. Both
are refused here, at `pytest_configure`, before a table is touched:

- **`app` is another checkout's** — the imported package does not live in the
  checkout whose `tests/` is running.
- **The test database is another checkout's** — the URL names
  `dms_test_<slug>` (the per-checkout name bootstrap and `scripts/dev/check`
  give it: `root` for the main checkout, the worktree folder's slug for a
  worktree) but not this checkout's own. A database derived from this one
  (`<own>_gw0`, a scratch `<own>_…`) is this checkout's. A name outside the
  `dms_test_` scheme (CI's `dms_platform_test`) is not judged: it belongs to
  no checkout.

Pure functions, so the refusal itself is tested
(`tests/test_checkout_guard.py`).
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
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    if len(out) != 2:
        return False
    git_dir, common = out[0], out[1]
    common_path = Path(common) if os.path.isabs(common) else (root / common)
    return os.path.normpath(git_dir) != os.path.normpath(str(common_path.resolve()))


def foreign_app(root: Path, app_file: str) -> str | None:
    """The refusal when `app_file` (`app.__file__`) is not under `root`."""

    app_dir = Path(app_file).resolve().parent
    if app_dir == (root / "app").resolve():
        return None
    return (
        f"this run imports another checkout's code: `app` is {app_dir}, but the tests are {root / 'tests'}. "
        f"The `pytest` on PATH belongs to another checkout's .venv. Run this checkout's own: "
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
