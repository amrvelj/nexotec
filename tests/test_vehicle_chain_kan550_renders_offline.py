"""KAN-174 for KAN-550's vehicle-chain migration `bb6e760d9464`: `alembic
upgrade a7c2d94e1b35:bb6e760d9464 --sql` renders, exit 0, with no database —
and renders the step's values, never `NULL`.

The review of KAN-550 found the first draft passing its parameters to
`execute()`: online they bound, offline (`literal_binds`, `alembic/env.py`)
they rendered as `NULL`, so a rendered script applied by hand would have
stamped the revision while its first two steps matched nothing.
"""

import os
import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_kan550_migration_renders_offline_with_its_values():
    run = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "a7c2d94e1b35:bb6e760d9464", "--sql"],
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    statements = [line for line in run.stdout.splitlines() if line.startswith(("DELETE", "UPDATE vehicle_"))]
    assert len(statements) == 7
    assert not any("NULL" in line for line in statements), statements
    assert sum("provider = 'auto_i_dat_mock'" in line for line in statements) == 6
    assert any("SET vehicle_kind = '01'" in line and "vehicle_kind = '1'" in line for line in statements)
    assert any("provider_code IN ('A', 'M')" in line for line in statements)
    assert any("code_group ~ '^[0-9]+$'" in line for line in statements)
