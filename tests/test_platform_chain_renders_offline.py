"""KAN-174 for the platform chain from KAN-204 up: `alembic upgrade
<from>:<platform head> --sql` renders, exit 0, with no database at all.

`.claude/rules/migrations.md` asks every new migration to render offline —
DDL as usual, a data step either written as literal SQL or guarded by
`context.is_offline_mode()`. The revisions on `main` below KAN-204 predate
the rule and some do not render (README, "Database migrations"), so the
range starts at the revision KAN-204 descends from and ends at the platform
head, whatever migrations are added above it. KAN-204's own seed of the
holiday calendars is rendered as INSERTs (34 calendars, one multi-row INSERT
of holidays each) — the review found it crashing on an untyped UUID column.
"""

import os
import pathlib
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_KAN_204 = "83d8634ff337"


def _platform_head(script: ScriptDirectory) -> str:
    """The one head whose history contains KAN-204 — the platform chain's."""

    [head] = [h for h in script.get_heads() if any(r.revision == _KAN_204 for r in script.iterate_revisions(h, "base"))]
    return head


def test_the_platform_chain_from_kan204_up_renders_offline():
    script = ScriptDirectory.from_config(Config(str(_REPO_ROOT / "alembic.ini")))
    start = script.get_revision(_KAN_204).down_revision
    assert isinstance(start, str)
    head = _platform_head(script)

    run = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", f"{start}:{head}", "--sql"],
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    assert run.stdout.count("INSERT INTO holiday_calendar ") == 34
    assert run.stdout.count("INSERT INTO holiday ") == 34
    assert "INSERT INTO location (" in run.stdout  # FR-A-03's first locations, as SQL
    assert "-- KAN-204 counts skipped" in run.stdout
